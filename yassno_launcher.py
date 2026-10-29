"""
Yassno Overlay — лаунчер: настройка и запуск оверлея для Le Mans Ultimate и rFactor 2.

 - вверху плитки игр (выбранная крупная и зелёная): у каждой игры свой профиль настроек;
 - слева список панелей оверлея, справа выбранная панель: предпросмотр и все её настройки;
 - сам определяет, какая игра запущена, и запускает оверлей с профилем этой игры.

Запуск: run-launcher.cmd (или python yassno_launcher.py). Только стандартная библиотека.
"""
import array
import struct
import bisect
import gzip
import uuid
import json
import glob
import re
import zipfile
import shutil
import math
import os
import queue
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser
import tkinter as tk
import tkinter.font as tkfont
from tkinter import colorchooser, filedialog, messagebox, ttk

APP_VERSION = "2.5.25"
HERE = os.path.dirname(os.path.abspath(__file__))
OVERLAY = os.path.join(HERE, "yassno_overlay.py")
PROFILES = os.path.join(HERE, "profiles")
LOGS = os.path.join(HERE, "logs")
LAUNCHER_CFG = os.path.join(HERE, "launcher.json")
CFG_VERSION = 4                                       # должен совпадать с CFG_VERSION в yassno_overlay.py
MAP_DIR = os.path.join(HERE, "maps")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

GAMES = {"lmu": "Le Mans Ultimate", "rf2": "rFactor 2", "iracing": "iRacing"}
DEFAULT_PROCS = {"lmu": "Le Mans Ultimate.exe, LMU.exe", "rf2": "rFactor2.exe", "iracing": "iRacingSim64DX11.exe, iRacingSim64DX12.exe"}

ELEMENTS = [("standings", "Таблица (стендинг)"), ("relative", "Релатив"), ("pedals", "Ввод: руль, педали, передача"),
            ("shiftlight", "Шифт-лайт"), ("trackmap", "Карта трассы и ветер"), ("radar", "Радар"),
            ("weather", "Погода"), ("lights", "Стартовые огни"), ("limiter", "Пит-лимитер"),
            ("fuel", "Топливный калькулятор"), ("delta", "Дельта к лучшему кругу"), ("alerts", "Предупреждения"),
            ("tyres", "Шины и тормоза"), ("pitplan", "Пит-стоп и стинт"), ("gaps", "Соперники: гэпы и тренд"),
            ("engine", "Двигатель и расход"), ("sesstime", "Таймер сессии"), ("clock", "Локальное время"), ("speedo", "Спидометр")]
# список панелей слева: (ключ страницы, название, ключ включения или None)
PAGES = [("general", "Общие и цвета", None), ("standings", "Таблица", "standings"), ("relative", "Релатив", "relative"),
         ("pedals", "Ввод и шифт-лайт", "pedals"), ("trackmap", "Карта и ветер", "trackmap"), ("radar", "Радар", "radar"),
         ("weather", "Погода", "weather"), ("lights", "Стартовые огни", "lights"), ("limiter", "Пит-лимитер", "limiter"),
         ("fuel", "Топливо", "fuel"), ("delta", "Дельта", "delta"), ("alerts", "Предупреждения", "alerts"),
         ("tyres", "Шины и тормоза", "tyres"), ("pitplan", "Пит-стоп и стинт", "pitplan"), ("gaps", "Соперники", "gaps"), ("engine", "Двигатель и расход", "engine"),
         ("sesstime", "Таймер сессии", "sesstime"), ("clock", "Локальное время", "clock"), ("speedo", "Спидометр", "speedo"),
         ("apps", "Доп. программы", None)]
STEAM_IDS = {"lmu": "2399420", "rf2": "365960", "iracing": "266410"}           # запуск игр через Steam, если путь к игре не задан
COMPANIONS = [("racelab", "RaceLab"), ("simhub", "SimHub"), ("crewchief", "Crew Chief"), ("tradingpaints", "Trading Paints")]
COMPANION_GAMES = {"tradingpaints": ("iracing",)}          # программы, которые есть только для одной игры


# официальные страницы скачивания (прямые ссылки на файлы меняются с каждой версией, поэтому — страницы загрузки)
APP_DOWNLOAD = {"racelab": "https://racelab.app/", "simhub": "https://www.simhubdash.com/download-2/",
                "crewchief": "https://thecrewchief.org/forumdisplay.php?28-Download-and-Links",
                "tradingpaints": "https://tradingpaints.com/install"}


def companion_runs(key, game):
    """Запускается ли программа для этой игры (в списке она видна всегда)."""
    return game in COMPANION_GAMES.get(key, tuple(GAMES))


def companions_for(game):
    return list(COMPANIONS)
THEME_DEFAULT = {"accent": "#f2b705", "text": "#eef1f5", "dim": "#8791a0", "panel": "#161b24",
                 "background": "#0b0e13", "border": "#2a3140", "me": "#4da3ff",
                 "throttle": "#2ee26d", "brake": "#ff3b30", "clutch": "#4a90ff"}
THEME_NAMES = {"accent": "Акцент (заголовки, таймер)", "text": "Основной текст", "dim": "Второстепенный текст",
               "panel": "Панели и шапки", "background": "Фон таблицы", "border": "Рамки", "me": "Моя строка",
               "throttle": "Газ", "brake": "Тормоз", "clutch": "Сцепление"}
PAGE_COLORS = {"general": list(THEME_NAMES), "standings": ["accent", "text", "dim", "background", "me", "border"],
               "relative": ["background", "me", "text", "dim"], "pedals": ["throttle", "brake", "clutch", "panel", "accent"],
               "trackmap": ["panel", "border", "text"], "radar": ["accent"], "weather": ["panel", "text", "dim"],
               "lights": [], "limiter": [], "fuel": ["panel", "text", "dim"], "apps": [],
               "delta": ["panel", "text"], "alerts": [], "tyres": ["panel", "text", "dim"], "pitplan": ["panel", "text", "dim"],
               "gaps": ["panel", "text", "dim"], "engine": ["panel", "text", "dim"], "sesstime": ["panel", "text", "dim"], "clock": ["panel", "text", "dim"], "speedo": ["panel", "text", "dim", "accent"]}
PAGE_NOTES = {
    "apps": "Программы, которые запускаются вместе с оверлеем этой игры (например, RaceLab, SimHub, Crew Chief). Для каждой игры свой набор.",
    "standings": "Главная таблица. Колонки включаются и выключаются переключателями ниже, предпросмотр показывает результат.",
    "relative": "Сколько машин впереди и позади, какие колонки показывать, размер и прозрачность. Всегда без разделения по классам.",
    "pedals": "Передача, скорость, руль, график газа/тормоза, столбики педалей и полоса шифт-лайта по оборотам.",
    "trackmap": "Карта рисуется сама по вашему проезду (пара кругов) и сохраняется. В углу — компас ветра.",
    "radar": "Вы в центре, соперники вокруг. Радар прозрачный, пока рядом никого нет. Красные дуги — машина сбоку.",
    "weather": "Выберите, какие карточки погоды показывать. Окно подстраивается под их число.",
    "lights": "Появляются только на стартовой решётке и горят синхронно с игрой. Цвета ламп фиксированные.",
    "limiter": "Зелёный — лимитер включён, красный — выключен, когда вы на пит-лейне. В остальное время скрыт.",
    "fuel": "Уровень топлива, расход за последний круг и среднее за N кругов, сколько нужно до финиша.",
    "delta": "На сколько секунд вы быстрее (зелёный) или медленнее (красный) лучшего круга в этой же точке трассы. Нужен один чистый круг без боксов.",
    "alerts": "Плашка появляется, только когда есть что сказать: жёлтый флаг, штраф, мало топлива или энергии, перегрев шин и тормозов, дождь, последний круг.",
    "tyres": "Температура, давление, остаток протектора и температура тормозов по каждому колесу.",
    "engine": "Температура воды и масла, расход топлива и остаток резины по каждому колесу. Виды: обычный, компактный (одна строка), циферблаты.",
    "pitplan": "Стинт, на сколько кругов хватит топлива и энергии, когда заезжать, таймеры пит-лейна и стоянки, время за рулём у каждого пилота.",
    "sesstime": "Название сессии (практика, квалификация, гонка) и таймер: сколько осталось, какой круг или сколько прошло. Те же данные, что в шапке таблицы, но отдельным окном.",
    "speedo": "Скорость, передача и обороты. Виды: дуга оборотов, круглый циферблат со стрелкой, тахометр, лента светодиодов, цифровой и минимальный. Выбор вида ниже.",
    "clock": "Реальное время на вашем компьютере крупными цифрами. Удобно, чтобы не терять счёт времени в длинной гонке.",
    "gaps": "Гэпы до соседей по классу спереди и сзади, как они меняются от круга к кругу и через сколько кругов вас догонят или вы догоните.",
}
MODES = {"stand_mode": [("table", "Таблица"), ("compact", "Компактная: топ-3 и соседи"), ("ticker", "Бегущая строка")],
         "map_mode": [("full", "Полная карта"), ("linear", "Линейная: круг полосой"), ("local", "Местная: вокруг машины")],
         "delta_ref": [("best", "К лучшему кругу"), ("last", "К прошлому кругу")],
         "layout_mode": [("off", "Одна для всех"), ("session", "По типу сессии: практика / квалификация / гонка"),
                         ("class", "По классу машины")],
         "delta_theme": [("full", "Стандартная: полная панель"), ("bar", "Только полоска с числом"),
                         ("sectors", "Время круга, прогноз и сектора"), ("ring", "Кольцо со шкалой"), ("wide", "Широкая полоса"),
                         ("pill", "Тонкая линия и капсулы"), ("tick", "Тонкая полоса с плашкой"),
                         ("segments", "Сегменты"), ("chevrons", "Стрелки")],
         "delta_view": [("full", "Полная панель"), ("bar", "Только полоска с числом"),
                        ("sectors", "Время круга, прогноз и сектора")],
         "wheel_style": [("round", "Классика: круглый"), ("formula", "Формульный"), ("gt", "GT: со срезом снизу"),
                         ("rally", "Раллийный: толстый обод"), ("bar", "Полоса поворота")],
         "weather_view": [("cards", "Карточки столбиком"), ("compact", "Компактный: одной строкой")],
         "win_style": [("classic", "Классический: скруглённые, красная метка"), ("square", "Строгий: прямые углы"),
                       ("soft", "Мягкий: сильно скруглённые"), ("minimal", "Минимализм: без цветной метки"),
                       ("accent", "Акцентный: метка и рамка цвета акцента")],
         "map_style": [("classic", "Классика: серая трасса"), ("black", "Чёрная, как в RaceLab")],
         "speedo_view": [("arc", "Дуга оборотов: передача и скорость"), ("dial", "Круглый циферблат со стрелкой скорости"),
                         ("tacho", "Тахометр со стрелкой и скоростью"), ("led", "Лента светодиодов, передача и скорость"),
                         ("digital", "Цифровой: крупная скорость и полоски"), ("minimal", "Минимальный: передача и скорость")],
         "engine_view": [("table", "Обычный"), ("compact", "Компактный: одна строка"), ("gauges", "Циферблаты")],
         "tyres_view": [("table", "Подробная"), ("compact", "Компактная: шины рисунком"),
                       ("wear", "Износ: остаток и за круг")]}
MAP_RADII = [200, 300, 500, 800, 1200]
UPDATE_FILES = ("yassno_overlay.py", "yassno_launcher.py", "yassno_svgmap.py", "README.txt")
BACKUP_DIR = os.path.join(HERE, "backup")
UPDATE_REPO, UPDATE_BRANCH = "Yassnnoo/yassno-overlay", "main"        # откуда лаунчер сам берёт обновления
UPDATE_EXT = (".py", ".txt", ".png", ".cmd")
UPDATE_BLOCK = ("profiles", "stats", "maps", "logs", "backup")        # свои данные обновление не трогает


def ver_tuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v))[:4])


def raw_url(path):
    return f"https://raw.githubusercontent.com/{UPDATE_REPO}/{UPDATE_BRANCH}/{path}"


def http_get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "yassno-overlay-updater", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read(8_000_000)
    return data


def safe_rel(p):
    """Путь из списка обновления: только внутри папки программы, только свои файлы."""
    p = str(p).replace("\\", "/").strip()
    parts = p.split("/")
    if (not p or p.startswith("/") or ":" in p or ".." in parts or len(parts) > 2 or "" in parts
            or parts[0] in UPDATE_BLOCK or not p.lower().endswith(UPDATE_EXT)):
        return None
    return p


def check_online_update():
    """Возвращает (сведения, ошибка). Берёт version.json из репозитория."""
    try:
        data = json.loads(http_get(raw_url("version.json") + "?t=%d" % time.time(), 15).decode("utf-8"))
        ver = str(data["version"])
        files = [f for f in (safe_rel(x) for x in data.get("files", [])) if f]
        if not ver_tuple(ver) or "yassno_overlay.py" not in files or "yassno_launcher.py" not in files:
            return None, "в version.json нет нужных файлов"
    except Exception as e:
        return None, str(e)
    return {"version": ver, "notes": str(data.get("notes", ""))[:700], "files": files,
            "newer": ver_tuple(ver) > ver_tuple(APP_VERSION)}, None


def apply_online_update(info):
    """Скачивает все файлы, проверяет, сохраняет копию старых в backup и заменяет. Возвращает текст ошибки или None."""
    got = {}
    try:
        for rel in info["files"]:
            body = http_get(raw_url(rel) + "?t=%d" % time.time())
            if not body:
                return f"пустой файл {rel}"
            if rel.endswith(".py"):
                compile(body, rel, "exec")                       # битый файл не ставим
            got[rel] = body
    except Exception as e:
        return f"{e}"
    try:
        bdir = os.path.join(BACKUP_DIR, time.strftime("%Y%m%d-%H%M%S"))
        os.makedirs(bdir, exist_ok=True)
        for name in UPDATE_FILES:
            src = os.path.join(HERE, name)
            if os.path.isfile(src):
                shutil.copyfile(src, os.path.join(bdir, name))
        for rel, body in got.items():
            dst = os.path.join(HERE, *rel.split("/"))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst + ".new", "wb") as f:
                f.write(body)
            os.replace(dst + ".new", dst)
    except OSError as e:
        return f"{e}"
    return None

HOTKEYS = [("lock", "Режим перемещения окон"), ("reset", "Вернуть окна на экран"), ("quit", "Выйти из оверлея"),
           ("hide", "Спрятать / показать оверлей"), ("tim", "Вид тайминга"), ("map", "Вид карты"),
           ("delta", "Вид дельты"), ("tyres", "Вид шин")]
HOTKEY_DEFAULTS = {"lock": "Ctrl+Shift+L", "reset": "Ctrl+Shift+R", "quit": "Ctrl+Shift+Q", "hide": "Ctrl+Shift+H",
                   "tim": "Ctrl+Shift+T", "map": "Ctrl+Shift+M", "delta": "Ctrl+Shift+D", "tyres": "Ctrl+Shift+Y"}
HK_MODS = ["Ctrl+Shift", "Ctrl+Alt", "Alt+Shift", "Ctrl", "Alt"]
HK_KEYS = [chr(c) for c in range(65, 91)] + [str(i) for i in range(10)] + [f"F{i}" for i in range(1, 13)]
# колонки таблицы: ключ, название, ширина в предпросмотре
STAND_COLS = [("delta", "Позиции ▲▼ (изменение места)", 14), ("logo", "Логотип марки", 18), ("tyre", "Шины", 22),
              ("ahead", "Отставание от передней", 50), ("lead", "Отставание от лидера", 50), ("best", "Лучший круг", 64),
              ("last", "Последний круг", 64), ("fuel", "Топливо", 36), ("ve", "Энергия", 46), ("status", "Статус", 36),
              ("irat", "iRating и лицензия (iRacing)", 88)]
PED_PARTS = [("gear", "Передача"), ("speed", "Скорость"), ("wheel", "Руль"),
             ("graph", "График газа и тормоза"), ("bars", "Столбики педалей")]
REL_COLS = [("pos", "Позиция"), ("num", "Номер"), ("tag", "Метка PIT"), ("irat", "iRating и лицензия (iRacing)")]
WX_CARDS = [("track", "Температура трассы"), ("air", "Температура воздуха"), ("wet", "Влажность трассы"), ("rain", "Осадки")]
OP_DEFAULT = {"standings": 0.94, "relative": 0.94, "pedals": 0.94, "trackmap": 0.94, "radar": 1.0,
              "weather": 0.94, "lights": 1.0, "limiter": 1.0, "fuel": 0.94, "delta": 0.94, "alerts": 1.0,
              "tyres": 0.94, "pitplan": 0.94, "gaps": 0.94, "engine": 0.94, "sesstime": 0.94, "clock": 0.94, "speedo": 0.94}
SCALE_KEYS = {"standings": "scale_standings", "relative": "scale_relative", "pedals": "scale_pedals", "weather": "scale_weather", "lights": "scale_lights",
              "limiter": "scale_limiter", "trackmap": "map_scale", "radar": "radar_scale", "fuel": "fuel_scale",
              "delta": "scale_delta", "alerts": "scale_alerts", "tyres": "scale_tyres", "pitplan": "scale_pitplan", "gaps": "scale_gaps", "engine": "scale_engine", "sesstime": "scale_sesstime", "clock": "scale_clock", "speedo": "scale_speedo"}
SCALE_DEFAULT = {"scale_standings": 1.0, "scale_relative": 1.0, "scale_pedals": 1.0, "scale_weather": 1.0, "scale_lights": 1.0, "scale_limiter": 1.0,
                 "map_scale": 1.0, "radar_scale": 1.0, "fuel_scale": 0.8, "scale_delta": 1.0, "scale_alerts": 1.0,
                 "scale_tyres": 1.0, "scale_pitplan": 1.0, "scale_gaps": 1.0, "scale_engine": 1.0, "scale_sesstime": 1.0, "scale_clock": 1.0, "scale_speedo": 1.0}
DEFAULTS = {**{k: True for k, _ in ELEMENTS}, "font": 13, "family": "", "rows": 12,
            "hide_garage": True, "class_only": False, **SCALE_DEFAULT, **{"op_" + k: v for k, v in OP_DEFAULT.items()},
            "radar_range": 40, "fuel_reserve": 0.0, "fuel_avg": 5,
            "rel_ahead": 3, "rel_behind": 3, "ped_seconds": 8, "shift_start": 82.0, "shift_flash": 98.0, "speed_mph": False,
            "map_wind": True, "map_dot": 6, "map_mirror": False, "map_rot": 0,
            "radar_arcs": True, "radar_fade": True, "lights_hold": 4,
            "map_nums": True, "map_sectors": True, "fuel_ve": True,
            "app_racelab": False, "app_simhub": False, "app_crewchief": False, "app_tradingpaints": False, "stats": True,
            "stand_mode": "table", "ticker_w": 1000, "ticker_speed": 70, "map_mode": "full", "map_radius": 500,
            "map_headup": True, "delta_ref": "best", "delta_range": 1.0, "delta_view": "full", "delta_theme": "full", "delta_show_session": True, "delta_show_mine": True, "delta_show_pred": True, "tyres_view": "table", "layout_mode": "off", "wheel_style": "round"}
DEFAULTS.update({"map_ir_svg": True, "weather_view": "cards", "map_style": "classic", "map_one_color": False, "map_frame": True,
                 **{"hg_" + k: True for k, _ in ELEMENTS if k not in ("standings", "shiftlight")}})
DEFAULTS.update({"tyres": False, "pitplan": False, "gaps": False, "engine": False, "engine_view": "table", "delta_sec_bar": True,
                 "sesstime": False, "clock": False, "speedo": False, "speedo_view": "arc", "speedo_max": 340, "clock_seconds": True, "st_session": True, "st_timer": True, "st_clock": True,
                 "win_style": "classic"})        # эти панели по умолчанию выключены
POS_KEYS = ["x", "y", "rel_x", "rel_y", "ped_x", "ped_y", "map_x", "map_y", "radar_x", "radar_y",
            "wx_x", "wx_y", "lights_x", "lights_y", "lim_x", "lim_y", "fuel_x", "fuel_y",
            "delta_x", "delta_y", "alerts_x", "alerts_y", "tyres_x", "tyres_y", "pitplan_x", "pitplan_y", "gaps_x", "gaps_y", "engine_x", "engine_y", "sesstime_x", "sesstime_y", "clock_x", "clock_y", "speedo_x", "speedo_y"]
RADAR_RANGES = [15, 20, 30, 40, 60, 80, 120]
RESERVES = ["0", "0.5", "1", "2"]

UI_THEMES = {
    "dark": dict(BG="#12161d", PANEL="#1a202a", FIELD="#222a36", FG="#e6e9ef", DIM="#8b95a5", ACCENT="#f2b705",
                 BORDER="#2a3140", ACTIVE="#2a3342", ACCENT_HI="#ffd24a", OFF="#3a4352", KNOB_OFF="#b4bcc9",
                 TILE_TEXT="#ffffff", ACCENT_FG="#111111"),
    "light": dict(BG="#f2f4f8", PANEL="#e3e7ee", FIELD="#ffffff", FG="#1b2230", DIM="#5f6b7c", ACCENT="#e0a100",
                  BORDER="#c3cad6", ACTIVE="#d4dae4", ACCENT_HI="#f2b705", OFF="#b8c0cd", KNOB_OFF="#ffffff",
                  TILE_TEXT="#ffffff", ACCENT_FG="#111111"),
}
UI_BG = UI_PANEL = UI_FIELD = UI_FG = UI_DIM = UI_ACCENT = UI_BORDER = UI_ACTIVE = UI_ACCENT_HI = ""
UI_OFF = UI_KNOB_OFF = UI_ACCENT_FG = ""
UI_GREEN, UI_GREEN_DK = "#2ee26d", "#1f9d4f"
UI_THEME_NAME = "dark"


def set_ui_theme(name):
    """Переключает палитру интерфейса лаунчера (глобальные цвета UI_*)."""
    global UI_BG, UI_PANEL, UI_FIELD, UI_FG, UI_DIM, UI_ACCENT, UI_BORDER, UI_ACTIVE, UI_ACCENT_HI, UI_OFF
    global UI_KNOB_OFF, UI_ACCENT_FG, UI_THEME_NAME
    if name == "system":
        name = system_theme()
    pal = UI_THEMES.get(name, UI_THEMES["dark"])
    UI_THEME_NAME = name if name in UI_THEMES else "dark"
    UI_BG, UI_PANEL, UI_FIELD, UI_FG, UI_DIM, UI_ACCENT = (pal[k] for k in ("BG", "PANEL", "FIELD", "FG", "DIM", "ACCENT"))
    UI_BORDER, UI_ACTIVE, UI_ACCENT_HI, UI_OFF = pal["BORDER"], pal["ACTIVE"], pal["ACCENT_HI"], pal["OFF"]
    UI_KNOB_OFF, UI_ACCENT_FG = pal["KNOB_OFF"], pal["ACCENT_FG"]


def system_theme():
    """Тема Windows: светлая или тёмная (по умолчанию тёмная)."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return "light" if winreg.QueryValueEx(k, "AppsUseLightTheme")[0] else "dark"
    except (ImportError, OSError):
        return "dark"


set_ui_theme("dark")
MOCK_BG = "#1d2431"                                     # «экран игры» в предпросмотре


# ---------------------------------------------------------------------------
# профили и настройки лаунчера
# ---------------------------------------------------------------------------
def profile_path(game):
    return os.path.join(PROFILES, f"{game}.json")


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def load_settings(game):
    data = read_json(profile_path(game))
    st = {k: data.get(k, v) for k, v in DEFAULTS.items()}
    if "layout_mode" not in data and data.get("session_layouts"):
        st["layout_mode"] = "session"                      # профиль со старой галочкой «по сессиям»
    hide = data.get("hide_cols", [])
    st["hide_cols"] = [k for k in hide if k in dict((a, b) for a, b, _ in STAND_COLS)] if isinstance(hide, list) else []
    for key, valid in (("rel_hide", REL_COLS), ("wx_hide", WX_CARDS), ("ped_hide", PED_PARTS)):
        got = data.get(key, [])
        st[key] = [k for k in got if k in dict(valid)] if isinstance(got, list) else []
    if "op_standings" not in data and isinstance(data.get("alpha"), (int, float)):
        for k in OP_DEFAULT:                              # профиль со старой общей прозрачностью
            if k in ("standings", "relative", "pedals", "trackmap", "weather", "fuel"):
                st["op_" + k] = float(data["alpha"])
    theme = dict(THEME_DEFAULT)
    if isinstance(data.get("theme"), dict):
        theme.update({k: v for k, v in data["theme"].items() if k in THEME_DEFAULT and isinstance(v, str)})
    st["theme"] = theme
    if game == "lmu":
        st["lights"] = False                               # в LMU стартовые огни рисует сама игра
    return st


def save_settings(game, st):
    """Записываем настройки в профиль, не трогая положение окон, которое хранит сам оверлей."""
    data = read_json(profile_path(game))
    # раскладки (по сессиям или классам) при запуске перекрывают общие настройки — переносим в них то, что вы изменили здесь
    lays = data.get("layouts")
    if isinstance(lays, dict) and lays:
        always = [k for k, _ in ELEMENTS] + ["stand_mode", "map_mode", "delta_view", "delta_theme", "tyres_view", "engine_view", "speedo_view"]
        for k in always + list(SCALE_DEFAULT):
            if k in st and data.get(k) != st[k]:
                for lay in lays.values():
                    if isinstance(lay, dict) and (k in always or k in lay):
                        lay[k] = st[k]
    data.update({k: v for k, v in st.items() if k != "theme"})
    data["alpha"] = st.get("op_standings", 0.94)
    data["theme"] = dict(st["theme"])
    data["ver"] = CFG_VERSION
    data["locked"] = data.get("locked", True)
    write_json(profile_path(game), data)



def reset_positions(game):
    data = read_json(profile_path(game))
    for k in POS_KEYS:
        data.pop(k, None)
    data.pop("screen_layouts", None)                  # раскладки по мониторам тоже сбрасываются
    data.pop("screen_last", None)
    data["ver"] = CFG_VERSION
    write_json(profile_path(game), data)


def default_launcher():
    return {"autostart": True, "close_with_game": True, "theme": "dark", "lang": "ru", "win_start": False,
            "start_min": False, "min_tray": False, "close_tray": False, "stop_on_exit": False, "poll_sec": 2.5,
            "apps_close": True, "launch_with_game": True, "telemetry": True, "i2_path": "", "tel_strips": [],
            "auto_update": True}


def load_launcher():
    d = read_json(LAUNCHER_CFG)
    procs = d.get("procs", {}) if isinstance(d.get("procs"), dict) else {}
    cfg = default_launcher()
    for k, dv in list(cfg.items()):
        if k in d and isinstance(d[k], type(dv)) or (k == "poll_sec" and isinstance(d.get(k), (int, float))):
            cfg[k] = d[k]
    if cfg["theme"] not in ("dark", "light", "system"):
        cfg["theme"] = "dark"
    if cfg["lang"] not in ("ru", "en"):
        cfg["lang"] = "ru"
    cfg["win_start"] = bool(cfg["win_start"]) or win_start_enabled()
    cfg["procs"] = {g: str(procs.get(g, DEFAULT_PROCS[g])) for g in GAMES}
    cfg["last_game"] = d.get("last_game") if d.get("last_game") in GAMES else next(iter(GAMES))
    gp, ap = d.get("game_paths", {}), d.get("app_paths", {})
    cfg["game_paths"] = {g: str(gp.get(g, "")) if isinstance(gp, dict) else "" for g in GAMES}
    cfg["app_paths"] = {k: str(ap.get(k, "")) if isinstance(ap, dict) else "" for k, _ in COMPANIONS}
    cfg["custom_apps"] = []
    for it in d.get("custom_apps", []) if isinstance(d.get("custom_apps"), list) else []:
        if isinstance(it, dict) and str(it.get("path", "")).strip():
            games = [g for g in it.get("games", []) if g in GAMES] if isinstance(it.get("games"), list) else []
            cfg["custom_apps"].append({"id": str(it.get("id") or "c_" + uuid.uuid4().hex[:8]),
                                       "name": str(it.get("name") or os.path.splitext(os.path.basename(str(it["path"])))[0]),
                                       "path": str(it["path"]), "games": games})
    return cfg


# ---------------------------------------------------------------------------
# определение запущенной игры (только по имени процесса)
# ---------------------------------------------------------------------------
def process_names():
    """Имена запущенных процессов (в нижнем регистре). Только Windows."""
    if os.name != "nt":
        return set()
    try:
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, timeout=8,
                             creationflags=NO_WINDOW).stdout.decode("latin-1", "ignore")
    except Exception:
        return set()
    names = set()
    for line in out.splitlines():
        line = line.strip()
        if line.startswith('"'):
            names.add(line[1:].split('"')[0].lower())
    return names


def detect_game(procs):
    """('lmu' | 'rf2' | None, имя найденного процесса)."""
    names = process_names()
    for game in ("lmu", "rf2", "iracing"):
        wanted = [p.strip().lower() for p in procs.get(game, "").split(",") if p.strip()]
        for w in wanted:
            if w in names:
                return game, w
    return None, ""


def similar_processes():
    keys = ("lmu", "mans", "rfactor", "rf2", "iracing")
    return sorted(n for n in process_names() if any(k in n for k in keys))


# ---------------------------------------------------------------------------
# запуск оверлея
# ---------------------------------------------------------------------------
def send_cmd(game, text):
    """Передаёт команду работающему оверлею (lock / unlock / toggle) через файл рядом с профилем."""
    path = os.path.splitext(profile_path(game))[0] + ".cmd"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return True
    except OSError:
        return False


def read_locked(game):
    """True — окна закреплены, False — режим перемещения, None — оверлей ещё не сообщил."""
    d = read_json(os.path.splitext(profile_path(game))[0] + ".state")
    return d.get("locked") if isinstance(d.get("locked"), bool) else None


class Runner:
    def __init__(self):
        self.proc = None
        self.game = None
        self.auto = False
        self.mode = ""                                 # "", "demo", "unlock"
        self._log = None
        self.on_start = None                           # вызывается после запуска оверлея: (game, mode, auto)
        self.on_stop = None                            # вызывается, когда оверлей остановлен совсем (не при перезапуске)

    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self, game, mode="", auto=False):
        self.stop(final=False)
        if not os.path.isfile(profile_path(game)):
            save_settings(game, load_settings(game))
        exe = sys.executable or "python"
        if exe.lower().endswith("python.exe"):
            w = exe[:-10] + "pythonw.exe"
            exe = w if os.path.isfile(w) else exe
        cmd = [exe, OVERLAY, "--game", game, "--config", profile_path(game)]
        if mode == "demo":
            cmd.append("--demo")
        elif mode == "unlock":
            cmd.append("--unlock")
        for ext in (".cmd", ".state"):
            try:
                os.remove(os.path.splitext(profile_path(game))[0] + ext)
            except OSError:
                pass
        os.makedirs(LOGS, exist_ok=True)
        self._log = open(os.path.join(LOGS, f"overlay-{game}.log"), "w", encoding="utf-8")
        self.proc = subprocess.Popen(cmd, cwd=HERE, stdin=subprocess.DEVNULL, stdout=self._log,
                                     stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
        self.game, self.auto, self.mode = game, auto, mode
        if self.on_start and mode != "demo":
            self.on_start(game, mode, auto)

    def stop(self, final=True):
        was = self.running()
        if was:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except Exception:
                self.proc.kill()
        self.proc = None
        if self._log:
            try:
                self._log.close()
            except OSError:
                pass
            self._log = None
        self.mode = ""
        if final and was and self.on_stop:
            self.on_stop()


# ---------------------------------------------------------------------------
# сопутствующие программы (RaceLab, SimHub, Crew Chief) и запуск игр
# ---------------------------------------------------------------------------
def app_candidates(key):
    """Где обычно лежат программы; первый существующий путь берём по умолчанию."""
    pf = [os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")]
    local = os.environ.get("LOCALAPPDATA", "")
    out = []
    if key == "simhub":
        out += [os.path.join(b, "SimHub", "SimHubWPF.exe") for b in pf]
    elif key == "crewchief":
        out += [os.path.join(b, "Britton IT Ltd", "CrewChiefV4", "CrewChiefV4.exe") for b in pf]
        out += [os.path.join(b, "CrewChiefV4", "CrewChiefV4.exe") for b in pf]
        out += [os.path.join(local, "CrewChiefV4", "CrewChiefV4.exe")] if local else []
    elif key == "tradingpaints":
        for base in ([local] if local else []) + pf:
            for folder in (("Rhinode LLC", "Trading Paints"), ("Trading Paints",), ("TradingPaints",),
                           ("Programs", "Trading Paints"), ("Programs", "TradingPaints")):
                d = os.path.join(base, *folder)
                out.append(os.path.join(d, "Trading Paints.exe"))
                try:                                           # на случай другого имени exe-файла
                    out += [os.path.join(d, f) for f in sorted(os.listdir(d))
                            if f.lower().endswith(".exe") and "trading" in f.lower() and "unins" not in f.lower()]
                except OSError:
                    pass
    elif key == "racelab":
        for base in ([local] if local else []) + pf:
            out += [os.path.join(base, "RaceLabApps", "RacelabApps.exe"),           # установка Squirrel: %LOCALAPPDATA%\racelabapps
                    os.path.join(base, "Programs", "RaceLab", "RaceLab.exe"), os.path.join(base, "RaceLab", "RaceLab.exe"),
                    os.path.join(base, "RaceLabApps", "RaceLab.exe"), os.path.join(base, "Programs", "racelab", "RaceLab.exe")]
    return out


def resolve_app(key, cfg):
    """Путь к программе: заданный вручную или найденный в обычных местах (пустая строка — не найдена)."""
    if key.startswith("c_"):
        it = next((c for c in cfg.get("custom_apps", []) if c["id"] == key), None)
        path = (it["path"] if it else "").strip().strip('"')
        return path if path and os.path.exists(path) else ""
    manual = (cfg.get("app_paths", {}).get(key) or "").strip().strip('"')
    if manual:
        return manual if os.path.exists(manual) else ""
    for c in app_candidates(key):
        if os.path.isfile(c):
            return c
    return ""


def spawn(path):
    """Запускает программу отдельно от лаунчера. Возвращает Popen или None (для ярлыков и т.п.)."""
    low = path.lower()
    if low.endswith((".lnk", ".url")) and hasattr(os, "startfile"):
        os.startfile(path)
        return None
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return subprocess.Popen([path], cwd=os.path.dirname(path) or None, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)


class Companions:
    """Запускает выбранные программы вместе с оверлеем и закрывает только те, что запустил сам."""

    def __init__(self):
        self.procs = {}

    def start(self, keys, cfg):
        """Возвращает список сообщений о том, что не удалось запустить."""
        problems = []
        if not keys:
            return problems
        running = process_names()
        for key in keys:
            name = dict(COMPANIONS).get(key) or next((c["name"] for c in cfg.get("custom_apps", []) if c["id"] == key), key)
            path = resolve_app(key, cfg)
            if not path:
                problems.append(f"{name}: не найден (проверьте путь на странице «Доп. программы»)")
                continue
            if os.path.basename(path).lower() in running:
                continue                                         # уже запущена — не трогаем
            try:
                p = spawn(path)
                if p is not None:
                    self.procs[key] = p
            except OSError as e:
                problems.append(f"{name}: {e}")
        return problems

    def stop(self):
        for key, p in list(self.procs.items()):
            try:
                if p.poll() is None:
                    p.terminate()
            except OSError:
                pass
        self.procs.clear()


def start_game_cmd(game, cfg):
    """Запускает игру: по заданному пути или через Steam. Возвращает текст ошибки или пустую строку."""
    path = (cfg.get("game_paths", {}).get(game) or "").strip().strip('"')
    try:
        if path:
            if not os.path.exists(path):
                return f"Не найден файл игры: {path}"
            if path.lower().endswith((".lnk", ".url", ".bat", ".cmd")) and hasattr(os, "startfile"):
                os.startfile(path)
            else:
                spawn(path)
            return ""
        if hasattr(os, "startfile"):
            os.startfile(f"steam://rungameid/{STEAM_IDS[game]}")
            return ""
        return "Запуск через Steam работает только в Windows."
    except OSError as e:
        return str(e)


# ---------------------------------------------------------------------------
# стиль
# ---------------------------------------------------------------------------
def setup_style(root):
    st = ttk.Style(root)
    try:
        st.theme_use("clam")
    except tk.TclError:
        pass
    root.configure(bg=UI_BG)
    st.configure(".", background=UI_BG, foreground=UI_FG, fieldbackground=UI_FIELD, bordercolor=UI_BORDER,
                 lightcolor=UI_PANEL, darkcolor=UI_PANEL, troughcolor=UI_FIELD, focuscolor=UI_BG)
    st.configure("TFrame", background=UI_BG)
    st.configure("TLabel", background=UI_BG, foreground=UI_FG)
    st.configure("Dim.TLabel", foreground=UI_DIM)
    st.configure("Head.TLabel", font=("Segoe UI Semibold", 11))
    st.configure("Title.TLabel", font=("Segoe UI Semibold", 15))
    st.configure("TLabelframe", background=UI_BG, bordercolor=UI_BORDER)
    st.configure("TLabelframe.Label", background=UI_BG, foreground=UI_ACCENT, font=("Segoe UI Semibold", 10))
    st.configure("TCheckbutton", background=UI_BG, foreground=UI_FG)
    st.map("TCheckbutton", background=[("active", UI_BG)])
    st.configure("TButton", background=UI_PANEL, foreground=UI_FG, padding=(10, 5), borderwidth=1)
    st.map("TButton", background=[("active", UI_ACTIVE)])
    st.configure("Accent.TButton", background=UI_ACCENT, foreground=UI_ACCENT_FG, font=("Segoe UI Semibold", 10))
    st.map("Accent.TButton", background=[("active", UI_ACCENT_HI)])
    st.configure("TNotebook", background=UI_BG, borderwidth=0)
    st.configure("TNotebook.Tab", background=UI_PANEL, foreground=UI_DIM, padding=(18, 8), font=("Segoe UI Semibold", 10))
    st.map("TNotebook.Tab", background=[("selected", UI_FIELD)], foreground=[("selected", UI_FG)])
    st.configure("TCombobox", fieldbackground=UI_FIELD, background=UI_PANEL, foreground=UI_FG, arrowcolor=UI_FG)
    st.map("TCombobox", fieldbackground=[("readonly", UI_FIELD)], foreground=[("readonly", UI_FG)])
    st.configure("TSpinbox", fieldbackground=UI_FIELD, background=UI_PANEL, foreground=UI_FG, arrowcolor=UI_FG)
    st.configure("TEntry", fieldbackground=UI_FIELD, foreground=UI_FG)
    st.configure("Horizontal.TScale", background=UI_ACCENT, troughcolor=UI_OFF, bordercolor=UI_BG,
                 lightcolor=UI_ACCENT, darkcolor=UI_ACCENT)
    st.map("Horizontal.TScale", background=[("active", UI_ACCENT_HI)])
    st.configure("Active.TButton", background=UI_GREEN_DK, foreground="#ffffff", font=("Segoe UI Semibold", 10))
    st.map("Active.TButton", background=[("active", UI_GREEN)])
    st.configure("Treeview", background=UI_FIELD, fieldbackground=UI_FIELD, foreground=UI_FG, rowheight=22, borderwidth=0)
    st.configure("Treeview.Heading", background=UI_PANEL, foreground=UI_FG, font=("Segoe UI Semibold", 9), relief="flat")
    st.map("Treeview", background=[("selected", UI_ACCENT)], foreground=[("selected", UI_ACCENT_FG)])
    st.map("Treeview.Heading", background=[("active", UI_ACTIVE)])
    for opt, val in (("background", UI_FIELD), ("foreground", UI_FG), ("selectBackground", UI_ACCENT),
                     ("selectForeground", UI_ACCENT_FG)):
        root.option_add("*TCombobox*Listbox." + opt, val)


def mix(c1, c2, t):
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(int(a[k] + (b[k] - a[k]) * max(0.0, min(1.0, t))) for k in range(3))


def valid_hex(c):
    return isinstance(c, str) and len(c) == 7 and c.startswith("#") and all(ch in "0123456789abcdefABCDEF" for ch in c[1:])




# ---------------------------------------------------------------------------
# язык интерфейса
# ---------------------------------------------------------------------------
LANG = "ru"
EN = {}                                   # русская строка -> английская (заполняется ниже)
EN_PREFIX = []                            # (русский префикс, английский префикс) для строк с подстановками
EN_SUFFIX = []


def set_lang(code):
    global LANG
    LANG = code if code in ("ru", "en") else "ru"


def tr(s):
    if LANG == "ru" or not isinstance(s, str) or not s:
        return s
    got = EN.get(s)
    if got is not None:
        return got
    for ru, en in EN_PREFIX:
        if s.startswith(ru):
            return en + en_rest(s[len(ru):])
    return en_rest(s)


EN.update({
    "Износ: остаток и за круг": "Wear: remaining and per lap", "остаток % и износ за круг": "remaining % and wear per lap",
    "Остаток шин": "Tyres left", "Износ за круг": "Wear per lap",
    "лаунчер": "launcher", "оверлей": "overlay",
    "Раскладки окон": "Window layouts", "Одна для всех": "One for everything",
    "По типу сессии: практика / квалификация / гонка": "By session: practice / qualifying / race", "По классу машины": "By car class",
    "класс": "class", "практика": "practice", "квалификация": "qualifying", "гонка": "race", "пока ничего": "nothing yet",
    "Сохранено: ": "Saved: ", "Сбросить сохранённые раскладки": "Reset saved layouts",
    "Окна, их положение, размеры и виды запоминаются сами: настройте их в режиме перемещения, и оверлей подставит нужную раскладку при смене класса или сессии.":
        "Windows, their positions, sizes and views are remembered automatically: set them up in move mode (or by right-click) and the overlay applies the right layout when the class or session changes.",
    "Удалить все сохранённые раскладки (по сессиям и по классам)? Текущие настройки останутся.": "Delete all saved layouts (by session and by class)? Current settings stay.",
    "Все сессии в один CSV…": "All sessions to one CSV…", "Пока нет сохранённых сессий.": "No saved sessions yet.",
    "в Google Таблицах: Файл → Импортировать → Загрузка": "in Google Sheets: File → Import → Upload",
    "ПИЛОТ": "DRIVER", "У каждого пилота свои раскладка окон, виды и цвета.": "Each driver has their own window layout, views and colours.",
    "Новый пилот": "New driver", "Имя пилота (копия текущих настроек):": "Driver name (copy of the current settings):",
    "Такой уже есть": "Already exists", "Пилот с таким именем уже существует.": "A driver with this name already exists.",
    "Основной профиль удалить нельзя.": "The main profile cannot be deleted.", "Удалить пилота": "Delete driver",
    "Обновление программы": "Program update", "Установить из zip…": "Install from zip…", "Откатить": "Roll back",
    "Оверлей": "Overlay", "Лаунчер": "Launcher", "Архив с обновлением": "Update archive",
    "Время круга": "Lap time", "Сектора": "Sectors", "Топливо": "Fuel", "Шины": "Tyres", "Скорость": "Speed",
    "график появится после двух кругов": "the chart appears after two laps", "лучший ": "best ", "расход за круг, л": "fuel per lap, l",
    "время круга": "lap time", "График появится после двух кругов": "The chart appears after two laps",
    "S1 к лучшему": "S1 vs best", "S2 к лучшему": "S2 vs best", "S3 к лучшему": "S3 vs best",
    "Горячие клавиши оверлея": "Overlay hotkeys", 
    "Вернуть окна на экран": "Bring windows back", "Выйти из оверлея": "Quit the overlay",
    "Спрятать / показать оверлей": "Hide / show the overlay", "Вид тайминга": "Timing view", "Вид карты": "Map view",
    "Вид дельты": "Delta view", "Вид шин": "Tyres view", "Стандартные": "Defaults",
    "Применяются при следующем запуске оверлея.": "Applied the next time the overlay starts.",
    "Одинаковое сочетание у двух действий — не сохранено.": "Two actions share one combination — not saved.",
    "Сохранено. Применится при следующем запуске оверлея.": "Saved. Applied the next time the overlay starts.",
    "Перенос настроек": "Move settings", "Экспорт настроек…": "Export settings…", "Импорт настроек…": "Import settings…",
    "Диагностика": "Diagnostics", "Проверить систему…": "Check the system…", "Проверяю…": "Checking…",
    "Экспорт настроек": "Export settings", "Импорт настроек": "Import settings",
    "Вид": "View", "Полная панель": "Full panel", "Только полоска с числом": "Bar with the number only",
    "Подробная": "Detailed", "Компактная: шины рисунком": "Compact: tyres drawn",
    "Акцент (заголовки, таймер)": "Accent (headers, timer)", "Основной текст": "Main text", "Второстепенный текст": "Secondary text",
    "Панели и шапки": "Panels and headers", "Фон таблицы": "Table background", "Рамки": "Borders", "Моя строка": "My row",
    "Газ": "Throttle", "Тормоз": "Brake", "Сцепление": "Clutch",
    "Главная таблица. Колонки включаются и выключаются переключателями ниже, предпросмотр показывает результат.":
        "The main standings table. Switch columns on and off below; the preview shows the result.",
    "Сколько машин впереди и позади, какие колонки показывать, размер и прозрачность. Всегда без разделения по классам.":
        "How many cars ahead and behind, which columns to show, size and opacity. Always all classes together.",
    "Передача, скорость, руль, график газа/тормоза, столбики педалей и полоса шифт-лайта по оборотам.":
        "Gear, speed, steering wheel, throttle/brake graph, pedal bars and the RPM shift-light strip.",
    "Карта рисуется сама по вашему проезду (пара кругов) и сохраняется. В углу — компас ветра.":
        "The map draws itself from your driving (a couple of laps) and is saved. The wind compass is in the corner.",
    "Вы в центре, соперники вокруг. Радар прозрачный, пока рядом никого нет. Красные дуги — машина сбоку.":
        "You are in the centre, rivals around you. The radar is transparent while nobody is near. Red arcs mean a car alongside.",
    "Выберите, какие карточки погоды показывать. Окно подстраивается под их число.":
        "Choose which weather cards to show. The window adapts to their number.",
    "Появляются только на стартовой решётке и горят синхронно с игрой. Цвета ламп фиксированные.":
        "Shown only on the starting grid, in sync with the game. Lamp colours are fixed.",
    "Зелёный — лимитер включён, красный — выключен, когда вы на пит-лейне. В остальное время скрыт.":
        "Green: limiter on. Red: limiter off while you are in the pit lane. Hidden otherwise.",
    "Уровень топлива, расход за последний круг и среднее за N кругов, сколько нужно до финиша.":
        "Fuel level, last-lap consumption, average over N laps and how much you need to the finish.",
    "Таблица (стендинг)": "Standings table", "Релатив": "Relative", "Ввод: руль, педали, передача": "Input: wheel, pedals, gear",
    "Шифт-лайт": "Shift light", "Карта трассы и ветер": "Track map and wind", "Радар": "Radar", "Погода": "Weather",
    "Стартовые огни": "Start lights", "Пит-лимитер": "Pit limiter", "Топливный калькулятор": "Fuel calculator",
    "Общие и цвета": "General and colours", "Таблица": "Standings", "Ввод и шифт-лайт": "Input and shift light",
    "Карта и ветер": "Map and wind", "Топливо": "Fuel",
    "Позиции ▲▼ (изменение места)": "Positions ▲▼ (place change)", "Логотип марки": "Make logo", "Шины": "Tyres",
    "Отставание от передней": "Gap to car ahead", "Отставание от лидера": "Gap to leader", "Лучший круг": "Best lap",
    "Последний круг": "Last lap", "Энергия": "Energy", "Позиция": "Position", "Номер": "Number", "Метка PIT": "PIT tag",
    "Температура трассы": "Track temperature", "Температура воздуха": "Air temperature", "Влажность трассы": "Track wetness",
    "Осадки": "Precipitation",
    "Положение окон": "Window positions", "Вернуть все окна оверлея на стандартные места?": "Put all overlay windows back to their default places?",
    "Журнал оверлея": "Overlay log", "Тёмная": "Dark", "Светлая": "Light", "Как в Windows": "Same as Windows", "Русский": "Русский",
    "Запускать лаунчер вместе с Windows": "Start the launcher with Windows", "Запускать свёрнутым": "Start minimised",
    "Сворачивать в трей (значок у часов)": "Minimise to the tray (icon by the clock)",
    "Крестик окна сворачивает в трей, а не закрывает": "The window's close button hides to the tray instead of closing",
    "Сам определять игру и запускать оверлей": "Detect the game and start the overlay automatically",
    "Закрывать оверлей вместе с игрой": "Close the overlay together with the game",
    "Останавливать оверлей при выходе из лаунчера": "Stop the overlay when the launcher exits",
    "Yassno Overlay — лаунчер": "Yassno Overlay — launcher", "Рядом с лаунчером нет": "Next to the launcher there is no",
    "Открыть лаунчер": "Open launcher", "Остановить оверлей": "Stop overlay", "Выход": "Exit", "● запущена": "● running",
    "Показывать эту панель": "Show this panel", "Непрозрачность": "Opacity", "Шрифт": "Font", "Размер шрифта": "Font size",
    "Скрывать оверлей, когда машина в гараже": "Hide the overlay while the car is in the garage",
    "Какие панели показывать": "Panels to show", "Строк в таблице": "Rows in the table", "Только мой класс": "My class only",
    "Скрывать, когда машина в гараже": "Hide while the car is in the garage", "Колонки таблицы": "Table columns",
    "Сохранено, оверлей перезапущен.": "Saved, overlay restarted.",
    "Сохранено. Изменения применятся при следующем запуске оверлея.": "Saved. Changes apply the next time the overlay starts.",
    "Нет файла": "File missing", "Положение сброшено — применится при следующем запуске.": "Positions reset — applies on the next start.",
    "Карты трасс": "Track maps", "Стереть все сохранённые карты трасс?\nОни нарисуются заново за пару кругов.":
        "Erase all saved track maps?\nThey will be redrawn in a couple of laps.",
    "Журнала пока нет — оверлей ещё не запускался.": "No log yet — the overlay has not been started.", "Выбор цвета": "Choose a colour",
    "Ввод": "Input", "Карта": "Map", "Огни": "Lights", "Лимитер": "Limiter", "клик по панели — включить / выключить": "click a panel to switch it on / off",
    "ВЫ": "YOU", "КАРТА": "MAP", "ТОПЛИВО": "FUEL",
    "Внешний вид": "Appearance", "Запуск": "Startup", "Свёрнутое окно": "Minimised window",
    "Установить поддержку трея": "Install tray support", "Оверлей и игра": "Overlay and game", "Файлы": "Files", "Сброс": "Reset",
    "Вернуть настройки лаунчера к стандартным? Автозапуск с Windows будет выключен.":
        "Restore the launcher settings to defaults? Start with Windows will be turned off.",
    "Имена процессов игр": "Game process names", "Ищу похожие процессы…": "Looking for similar processes…",
    "Закрыть и оверлей тоже?\n\nДа — закрыть всё\nНет — оставить оверлей работать":
        "Close the overlay too?\n\nYes — close everything\nNo — leave the overlay running",
    "(авто)": "(auto)", "Цвета": "Colours", "Размер": "Size", "Машин впереди": "Cars ahead", "Машин позади": "Cars behind",
    "Колонки релатива": "Relative columns", "Цвета (общие для всего оверлея)": "Colours (shared by the whole overlay)",
    "Не удалось сохранить": "Could not save", "Оверлей запущен.": "Overlay started.",
    "Режим перемещения: двигайте окна мышью, затем Ctrl+Shift+L — закрепить.":
        "Move mode: drag the windows with the mouse, then Ctrl+Shift+L to lock them.",
    "Демо без игры (закрыть: Ctrl+Shift+Q или «Остановить оверлей»).": "Demo without the game (close: Ctrl+Shift+Q or “Stop overlay”).",
    "(журнал пуст)": "(log is empty)", "выключено": "off", "\nвыключено": "\noff", "последний круг": "last lap", "добавить": "to add",
    "Открыть папку профилей": "Open profiles folder", "Открыть папку журналов": "Open logs folder",
    "Открыть папку карт трасс": "Open track maps folder", "Открыть папку лаунчера": "Open launcher folder",
    "Устанавливаю…": "Installing…", "Не удалось открыть": "Could not open", "Настройки лаунчера": "Launcher settings",
    "Сам определять игру и запускать оверлей с её профилем": "Detect the game and start the overlay with its profile",
    "Лаунчер не запустился": "The launcher failed to start", "ПАНЕЛИ ОВЕРЛЕЯ": "OVERLAY PANELS", "Сохранить": "Save",
    "Сохранить и перезапустить оверлей": "Save and restart the overlay", "Сбросить положение окон": "Reset window positions",
    "Режим перемещения окон": "Window move mode", "Демо без игры": "Demo without the game", "Журнал": "Log",
    "Все панели оверлея сразу. Клик по панели в предпросмотре включает или выключает её. Шрифт и цвета общие для всего оверлея.":
        "All overlay panels at once. Click a panel in the preview to switch it on or off. Font and colours are shared by the whole overlay.",
    "Шифт-лайт (полоса огней по оборотам)": "Shift light (RPM light strip)", "Шифт-лайт: начало": "Shift light: starts at",
    "Шифт-лайт: мигание": "Shift light: flashes at", "График, секунд": "Graph length, seconds",
    "Скорость в милях в час (иначе км/ч)": "Speed in mph (otherwise km/h)",
    "Всё, что относится к самой программе. Настройки оверлея — на страницах игр.":
        "Everything about the program itself. Overlay settings are on the game pages.",
    "Тема": "Theme", "Язык": "Language", "Проверять запущенную игру каждые": "Check for a running game every", "сек.": "sec.",
    "Процессы игр…": "Game processes…", "Сбросить настройки лаунчера": "Reset launcher settings",
    "Профили оверлея (LMU и rFactor 2) не затрагиваются.": "Overlay profiles (LMU and rFactor 2) are not affected.",
    "Поддержка трея установлена.": "Tray support is installed.",
    "Для трея нужны пакеты pystray и pillow. Без них окно просто сворачивается на панель задач.":
        "The tray needs the pystray and pillow packages. Without them the window just minimises to the taskbar.",
    "ИГРА": "GAME", "Имена .exe через запятую — по ним лаунчер понимает, какая игра запущена.":
        "Comma-separated .exe names — the launcher uses them to tell which game is running.",
    "Игра не запущена": "Game is not running", "Оверлей: остановлен": "Overlay: stopped", "Игра": "Game",
    "Оверлей: работает для": "Overlay: running for", "Сбросить цвета": "Reset colours", "Размер карты": "Map size",
    "Размер точек машин": "Car dot size", "Таймер сессии": "Session timer", "Локальное время": "Local time", "Стиль окон": "Window style",
    "Шапка таблицы": "Table header", "Название сессии": "Session name", "Таймер: круг / осталось": "Timer: lap / time left",
    "Показывать секунды": "Show seconds", "Классический: скруглённые, красная метка": "Classic: rounded, red mark",
    "Строгий: прямые углы": "Strict: square corners", "Мягкий: сильно скруглённые": "Soft: heavily rounded",
    "Минимализм: без цветной метки": "Minimal: no colour mark", "Акцентный: метка и рамка цвета акцента": "Accent: mark and border in accent colour", "Компас ветра в углу": "Wind compass in the corner", "Зеркально отразить карту": "Mirror the map",
    "Поворот карты": "Map rotation", "Размер радара": "Radar size", "Дальность, м": "Range, m",
    "Красные дуги, когда машина сбоку": "Red arcs when a car is alongside", "Плавное проявление при приближении": "Fade in as cars approach",
    "ВИРТ. ЭНЕРГИЯ": "VIRT. ENERGY", "Дельта": "Delta", "Предупреждения": "Alerts", "Шины и тормоза": "Tyres and brakes",
    "Пит-стоп и стинт": "Pit stop and stint", "Соперники": "Rivals", "Дельта к лучшему кругу": "Delta to best lap",
    "Соперники: гэпы и тренд": "Rivals: gaps and trend", "Вид тайминга": "Timing view", "Таблица": "Table",
    "Компактная: топ-3 и соседи": "Compact: top 3 and neighbours", "Бегущая строка": "Ticker",
    "Вид карты": "Map view", "Полная карта": "Full map", "Линейная: круг полосой": "Linear: lap as a strip",
    "Местная: вокруг машины": "Local: around the car", "Местная карта": "Local map", "Радиус, м": "Radius, m",
    "«Вперёд» всегда вверх (иначе север сверху)": "Forward is always up (otherwise north is up)",
    "Сравнивать": "Compare", "К лучшему кругу": "To best lap", "К прошлому кругу": "To last lap",
    "Шкала, секунд (±)": "Scale, seconds (±)", "Ширина, пикселей": "Width, pixels", "Скорость, пикселей в секунду": "Speed, pixels per second",
    "Раскладки по сессиям: своя для практики, квалификации и гонки": "Per-session layouts: own for practice, qualifying and race",
    "к лучшему": "to best", "к прошлому": "to last", "эталон 1:41.230": "reference 1:41.230",
    "ШТРАФ  (1)": "PENALTY  (1)", "ЖЁЛТЫЙ ФЛАГ": "YELLOW FLAG", "ПОСЛЕДНИЙ КРУГ": "LAST LAP", "ДЕЛЬТА": "DELTA",
    "ШИНЫ И ТОРМОЗА": "TYRES AND BRAKES", "ПИТ-СТОП И СТИНТ": "PIT STOP AND STINT", "СОПЕРНИКИ": "RIVALS",
    "ВПЕРЕДИ": "AHEAD", "ПОЗАДИ": "BEHIND", "→ едет справа налево": "→ scrolls right to left", "за круг 6.2%": "per lap 6.2%", "СТАРТ": "START",
    "Номера мест на машинах": "Position numbers on cars", "Сектора и отметка старта/финиша": "Sectors and start/finish mark",
    "На карте": "On the map", "Оформление": "Appearance", "Панель": "Panel", "Шрифт и вид": "Font and look",
    "Какие панели показывать": "Panels to show", "Колонки таблицы": "Table columns", "Колонки релатива": "Relative columns",
    "Виртуальная энергия (LMU)": "Virtual energy (LMU)", "Показывать блок энергии и её расчёт": "Show the energy block and its calculation",
    "Остаток в %, расход за круг, среднее и сколько\nэнергии нужно до финиша — как для топлива.": "Level in %, use per lap, average and how much\nenergy is needed to the finish — same as for fuel.",
    "Запускать вместе с оверлеем этой игры": "Start together with this game's overlay", "Свои программы": "Your own programs",
    "+ Добавить программу…": "+ Add program…", "Параметры": "Options",
    "Здесь можно добавить любое приложение или ярлык — оно запустится вместе с оверлеем.": "Add any application or shortcut here — it starts together with the overlay.",
    "Запускать оверлей вместе с игрой (кнопка «Запустить игру»)": "Start the overlay with the game (\"Launch game\" button)",
    "Скорость в милях в час (иначе км/ч)": "Speed in mph (otherwise km/h)", "Полоса огней по оборотам": "Shift-light bar by RPM",
    "Начало": "Start", "Мигание": "Flash",
    "Поменять лево и право местами": "Swap left and right", "Карточки погоды": "Weather cards",
    "ничего не найдено (запустите игру и откройте окно заново)": "nothing found (start the game and reopen this window)",
    "Стереть сохранённые карты трасс": "Erase saved track maps", "Зелёный горит, секунд": "Green stays on, seconds",
    "Размер окна": "Window size", "Запас, кругов": "Reserve, laps", "Средний расход за": "Average consumption over", "кругов": "laps",
    "▶ Запустить игру": "▶ Start the game", "Доп. программы": "Extra programs", "Скачать программу": "Download the program",
    "Программы, которые запускаются вместе с оверлеем этой игры (например, RaceLab, SimHub, Crew Chief). Для каждой игры свой набор.":
        "Programs started together with this game's overlay (for example RaceLab, SimHub, Crew Chief). Each game has its own set.",
    "Запускать вместе с оверлеем этой игры:": "Start together with this game's overlay:", "Обзор…": "Browse…",
    "Закрывать эти программы вместе с оверлеем (только те, что запустил лаунчер)": "Close these programs together with the overlay (only the ones the launcher started)",
    "Путь можно не указывать, если программа стоит в обычном месте — лаунчер найдёт её сам. Уже запущенные программы повторно не открываются.":
        "You can leave the path empty if the program is in its usual place — the launcher will find it. Programs that are already running are not opened again.",
    "Найдена: ": "Found: ", "Файл не найден — проверьте путь": "File not found — check the path",
    "Не найдена — нажмите «Обзор…» и укажите программу": "Not found — click “Browse…” and pick the program",
    "Запуск игр": "Launching games", "Пусто — игра запускается через Steam.": "Empty — the game is launched through Steam.",
    "Игра уже запущена.": "The game is already running.", "Не удалось запустить игру": "Could not start the game",
    "Выберите программу": "Choose a program", "Выберите файл игры": "Choose the game file", "Программы и ярлыки": "Programs and shortcuts",
    "Все файлы": "All files", "запустится": "will start", "не запускается": "not started", "не найдена": "not found",
    " (демо)": " (demo)", " (режим перемещения)": " (move mode)", "Выберите": "Choose",
})
EN.update({
    "✔ Режим перемещения ВКЛ — нажмите, чтобы закрепить": "✔ Move mode ON — click to lock the windows",
    "Окна закреплены, режим перемещения выключен.": "Windows locked, move mode is off.",
    "Режим перемещения: двигайте окна мышью, затем нажмите эту кнопку ещё раз.": "Move mode: drag the windows with the mouse, then click this button again.",
    "Не удалось": "Failed", "Не получилось передать команду оверлею.": "Could not send the command to the overlay.",
    "Статистика": "Statistics", "Статистика сессий": "Session statistics", "Сессии": "Sessions", "Обновить": "Refresh",
    "Экспорт в CSV…": "Export to CSV…", "Удалить сессию": "Delete session", "Открыть папку статистики": "Open the statistics folder",
    "Пока пусто. Статистика пишется, пока работает оверлей: каждый круг сохраняется в папку stats.":
        "Nothing yet. Statistics are recorded while the overlay runs: every lap is saved to the stats folder.",
    "начало": "started", "лучшее": "best", "Круги": "Laps", "Лучший круг": "Best lap", "Средний (без пит)": "Average (no pit laps)",
    "Топливо, л/круг": "Fuel, L/lap", "Износ шин, %/круг": "Tyre wear, %/lap", "Макс. скорость": "Top speed", "Мин. скорость": "Min speed",
    " км/ч": " km/h", "График времён круга появится после двух кругов": "The lap-time chart appears after two laps",
    "Удалить выбранную сессию из статистики? Это нельзя отменить.": "Delete the selected session from the statistics? This cannot be undone.",
    "Не удалось удалить": "Could not delete",
    "Записывать статистику сессий (вкладка «Статистика»)": "Record session statistics (the “Statistics” tab)",
    "Круг": "Lap", "Время": "Time", "Топливо, л": "Fuel, L", "Остаток, л": "Left, L", "Износ/круг, %": "Wear/lap, %", "Пит": "Pit",
})
EN_PREFIX.extend([
    ("Запустить оверлей (", "Start overlay ("), ("Сохранено: ", "Saved: "), ("Стёрто карт: ", "Maps erased: "),
    ("Рядом с лаунчером не найден ", "Next to the launcher this file was not found: "),
    ("Сейчас запущено похожее: ", "Similar processes running now: "), ("Ошибка записана в logs/launcher-error.log:", "The error was written to logs/launcher-error.log:"),
    ("среднее (", "average ("), ("Запускаю ", "Starting "), ("Не найден файл игры: ", "Game file not found: "),
])
EN_SUFFIX[:] = [(" км/ч", " km/h"), (" кр.)", " laps)"), (" кр.", " laps"), (" л", " L"), (" м", " m")]


def en_rest(s):
    """Переводит хвосты вроде « кр.)», « л», « м» в числовых подписях."""
    for ru, en in EN_SUFFIX:
        if s.endswith(ru):
            return s[:-len(ru)] + en
    return s


def translate_tree(widget):
    """Переводит подписи всех виджетов внутри (кнопки, надписи, рамки, переключатели)."""
    if LANG == "ru":
        return
    try:
        kids = widget.winfo_children()
    except tk.TclError:
        return
    for w in kids:
        try:
            txt = w.cget("text")
        except (tk.TclError, AttributeError):
            txt = ""
        if isinstance(txt, str) and txt:
            new = tr(txt)
            if new != txt:
                try:
                    w.configure(text=new)
                except tk.TclError:
                    pass
        translate_tree(w)


class TrCanvas(tk.Canvas):
    """Холст предпросмотра: подписи на нём тоже переводятся."""

    def create_text(self, *a, **k):
        if "text" in k and LANG != "ru":
            t = tr(k["text"])
            if t == k["text"] and isinstance(t, str) and t.endswith(" м"):
                t = t[:-2] + " m"
            k["text"] = t
        return super().create_text(*a, **k)


def mb_info(title, msg):
    return messagebox.showinfo(tr(title), tr(msg))


_WHEEL = {"areas": [], "root": None}


def enable_wheel(canvas, area):
    """Колесо мыши листает canvas, когда курсор над area (работает и над вложенными виджетами)."""
    root = canvas.winfo_toplevel()
    _WHEEL["areas"].append((canvas, area))
    if _WHEEL["root"] is not root:
        _WHEEL["root"] = root

        def handler(e):
            try:
                w = root.winfo_containing(e.x_root, e.y_root)
            except (KeyError, tk.TclError):
                return
            while w is not None:
                for cv, ar in list(_WHEEL["areas"]):
                    try:
                        alive = cv.winfo_exists() and ar.winfo_exists()
                    except tk.TclError:
                        alive = False
                    if not alive:
                        _WHEEL["areas"].remove((cv, ar))
                    elif w is ar:
                        if cv.yview() != (0.0, 1.0):
                            cv.yview_scroll(-1 if e.delta > 0 else 1, "units")
                        return "break"
                w = getattr(w, "master", None)
        root.bind_all("<MouseWheel>", handler)


def mb_error(title, msg):
    return messagebox.showerror(tr(title), tr(msg))


def mb_yesno(title, msg):
    return messagebox.askyesno(tr(title), tr(msg))


def mb_yesnocancel(title, msg):
    return messagebox.askyesnocancel(tr(title), tr(msg))


# ---------------------------------------------------------------------------
# запуск вместе с Windows
# ---------------------------------------------------------------------------
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "YassnoOverlayLauncher"


def gui_python():
    exe = sys.executable or "python"
    if exe.lower().endswith("python.exe"):
        w = exe[:-10] + "pythonw.exe"
        if os.path.isfile(w):
            return w
    return exe


def set_win_start(on, minimized=False):
    """Добавляет или убирает лаунчер из автозагрузки Windows (раздел реестра текущего пользователя)."""
    try:
        import winreg
    except ImportError:
        return False
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            if on:
                cmd = f'"{gui_python()}" "{os.path.join(HERE, "yassno_launcher.py")}"' + (" --minimized" if minimized else "")
                winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(k, RUN_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False


def win_start_enabled():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, RUN_NAME)
            return True
    except (ImportError, OSError):
        return False


# ---------------------------------------------------------------------------
# значок в трее (нужны пакеты pystray и pillow; без них окно просто сворачивается)
# ---------------------------------------------------------------------------
def tray_supported():
    try:
        import pystray  # noqa: F401
        from PIL import Image  # noqa: F401
        return True
    except Exception:
        return False


def make_tray(on_show, on_stop, on_quit, running_game):
    import pystray
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((4, 4, 60, 60), fill=(31, 157, 79, 255))
    d.rectangle((22, 18, 42, 46), fill=(255, 255, 255, 255))
    menu = pystray.Menu(
        pystray.MenuItem(tr("Открыть лаунчер"), lambda *a: on_show(), default=True),
        pystray.MenuItem(tr("Остановить оверлей"), lambda *a: on_stop()),
        pystray.MenuItem(tr("Выход"), lambda *a: on_quit()))
    return pystray.Icon("yassno", img, "Yassno Overlay", menu)


class Toggle(tk.Frame):
    """Переключатель-«пилюля»: зелёный с ползунком справа — включено, серый с ползунком слева — выключено."""
    W, H = 34, 18

    def __init__(self, parent, text, var, bg=None, command=None, font=("Segoe UI", 10)):
        bg = bg or UI_BG
        super().__init__(parent, bg=bg, cursor="hand2")
        self.var, self.command, self.bg = var, command, bg
        self.cv = tk.Canvas(self, width=self.W, height=self.H, bg=bg, highlightthickness=0, cursor="hand2")
        self.cv.pack(side="left")
        self.lbl = tk.Label(self, text=text, bg=bg, fg=UI_FG, font=font, cursor="hand2", anchor="w")
        self.lbl.pack(side="left", padx=(8, 0))
        for w in (self, self.cv, self.lbl):
            w.bind("<Button-1>", self.flip)
        self._tid = var.trace_add("write", lambda *a: self.draw())
        self.bind("<Destroy>", self.on_destroy)
        self.draw()

    def on_destroy(self, e=None):
        if e is not None and e.widget is not self:
            return
        try:
            self.var.trace_remove("write", self._tid)
        except (tk.TclError, ValueError, AttributeError):
            pass

    def flip(self, e=None):
        self.var.set(not self.var.get())
        if self.command:
            self.command()

    def draw(self):
        try:
            on = bool(self.var.get())
            c, W, H = self.cv, self.W, self.H
            c.delete("all")
            r = H / 2
            col = UI_GREEN_DK if on else UI_OFF
            c.create_oval(1, 1, H - 1, H - 1, fill=col, outline=col)
            c.create_oval(W - H + 1, 1, W - 1, H - 1, fill=col, outline=col)
            c.create_rectangle(r, 1, W - r, H - 1, fill=col, outline=col)
            kx = W - r if on else r
            c.create_oval(kx - r + 3, 3, kx + r - 3, H - 3, fill="#ffffff" if on else UI_KNOB_OFF, outline="")
            self.lbl.config(fg=UI_FG if on else UI_DIM)
        except tk.TclError:
            pass


class GameTile(tk.Frame):
    """Плитка выбора: все одного размера. У игр — логотип: серый, пока игра не выбрана, и цветной у выбранной."""
    W, H = 220, 64
    BLUE, BLUE_HI = "#1d4f9c", "#2f6fd0"

    def __init__(self, parent, text, command, blue=False, static=False, game=None):
        super().__init__(parent, bg=UI_PANEL, cursor="hand2", highlightthickness=2, highlightbackground=UI_BORDER,
                         width=self.W, height=self.H)
        self.pack_propagate(False)
        self.text, self.command, self.blue = text, command, blue
        cur = "arrow" if static else "hand2"
        self.config(cursor=cur)
        self.imgs = {}
        for st in ("on", "off"):
            fp = os.path.join(HERE, "logos", f"tile_{game}_{st}.png") if game else ""
            try:
                self.imgs[st] = tk.PhotoImage(file=fp) if fp and os.path.isfile(fp) else None
            except tk.TclError:
                self.imgs[st] = None
        self.lbl = tk.Label(self, text=text, bg=UI_PANEL, fg=UI_DIM, font=("Segoe UI Semibold", 11), cursor=cur, bd=0)
        self.lbl.pack(expand=True, fill="both")
        self.sub = tk.Label(self, text="", bg=UI_PANEL, fg=UI_GREEN, font=("Segoe UI", 8, "bold"), cursor=cur, bd=0)
        if not static:
            for w in (self, self.lbl, self.sub):
                w.bind("<Button-1>", lambda e: self.command())
        self.set_state(False, False)

    def set_state(self, selected, running):
        if self.imgs.get("on"):
            img = self.imgs["on" if selected else "off"]
            edge = "#ffffff" if selected else UI_BORDER
            self.config(bg="#ffffff", highlightbackground=edge)
            self.lbl.config(image=img, text="", bg="#ffffff")
            self.sub.config(text=tr("● запущена") if running else "", bg="#ffffff", fg="#1a9a4a")
            if running:
                self.sub.place(relx=1.0, rely=0.0, anchor="ne", x=-4, y=1)
            else:
                self.sub.place_forget()
            return
        if selected:
            bg, edge, fg = UI_PANEL, "#ffffff", "#ffffff"
        elif self.blue:
            bg, edge, fg = self.BLUE, self.BLUE_HI, "#ffffff"
        else:
            bg, edge, fg = UI_PANEL, UI_BORDER, UI_DIM
        self.config(bg=bg, highlightbackground=edge)
        self.lbl.config(text=self.text, bg=bg, fg=fg, image="")
        self.sub.config(text=tr("● запущена") if running else "", bg=bg, fg=UI_GREEN)


# ---------------------------------------------------------------------------
# панель одной игры: слева список панелей, справа выбранная
# ---------------------------------------------------------------------------
class ProfilePanel(ttk.Frame):
    GH = 640                                            # высота общего предпросмотра
    PW, PH = 680, 440                                   # размер предпросмотра
    RW = 520                                            # ширина колонки настроек справа
    NAMES = ["A. FUOCO", "S. BUEMI", "K. ESTRE", "VLADIMIR", "A. LYNN", "F. ALBU", "J. ALOMAR", "P. KUBICA",
             "R. LIETZ", "D. HARPER", "N. CATSB", "N. MOLLER", "J. LOPEZ", "M. CRESS"]
    CLS = ["#ff4d4d", "#ff4d4d", "#ff4d4d", "#2fd27e", "#ff4d4d", "#3a8dff", "#3a8dff", "#3a8dff",
           "#2fd27e", "#2fd27e", "#2fd27e", "#2fd27e", "#2fd27e", "#2fd27e"]
    ROT = ["0°", "90°", "180°", "270°"]

    def __init__(self, parent, app, game):
        super().__init__(parent)
        self.app, self.game = app, game
        self._open, self._cards = {}, []
        st = load_settings(game)
        self.v = {}
        for k, dv in DEFAULTS.items():
            val = st[k]
            if k == "family":
                self.v[k] = tk.StringVar(value=val or "(авто)")
            elif k in MODES:
                self.v[k] = tk.StringVar(value=self.mode_label(k, val))
            elif k in ("radar_range", "map_radius"):
                self.v[k] = tk.StringVar(value=str(int(val)))
            elif k == "fuel_reserve":
                self.v[k] = tk.StringVar(value=f"{float(val):g}")
            elif k == "map_rot":
                self.v[k] = tk.StringVar(value=self.ROT[int(val) % 4])
            elif isinstance(dv, bool):
                self.v[k] = tk.BooleanVar(value=bool(val))
            elif isinstance(dv, int):
                self.v[k] = tk.IntVar(value=int(val))
            else:
                self.v[k] = tk.DoubleVar(value=float(val))
        # группы переключателей «показывать»: True — колонка / карточка видна
        self.cols = {k: tk.BooleanVar(value=k not in st["hide_cols"]) for k, _, _ in STAND_COLS}
        self.relc = {k: tk.BooleanVar(value=k not in st["rel_hide"]) for k, _ in REL_COLS}
        self.wxc = {k: tk.BooleanVar(value=k not in st["wx_hide"]) for k, _ in WX_CARDS}
        self.pedc = {k: tk.BooleanVar(value=k not in st["ped_hide"]) for k, _ in PED_PARTS}
        self.theme = {k: tk.StringVar(value=st["theme"][k]) for k in THEME_DEFAULT}
        self.page = "general"
        self.rows_ui = {}                               # ключ страницы -> виджеты строки списка
        self.swatches = []                              # (ключ цвета, виджет-образец) текущей страницы
        self.value_lbls = []                            # (переменная, подпись, формат) слайдеров текущей страницы
        self.hits = []
        self._pending = False
        self.th = dict(THEME_DEFAULT)
        self.build_ui()
        every = (list(self.v.values()) + list(self.theme.values()) + list(self.cols.values()) +
                 list(self.relc.values()) + list(self.wxc.values()) + list(self.pedc.values()))
        for var in every:
            var.trace_add("write", lambda *a: self.changed())
        self.show_page("general")

    def saved_layouts(self):
        lay = read_json(profile_path(self.game)).get("layouts")
        return sorted(lay) if isinstance(lay, dict) else []

    def refresh_layout_info(self):
        names = [k.replace("cls:", tr("класс") + " ") if k.startswith("cls:") else tr({"practice": "практика", "qualify": "квалификация", "race": "гонка"}.get(k, k))
                 for k in self.saved_layouts()]
        self.lay_info.config(text=tr("Окна, их положение, размеры и виды запоминаются сами: настройте их в режиме перемещения "
                                     "и оверлей подставит нужную раскладку при смене класса или сессии.") + "\n" +
                             tr("Сохранено: ") + (", ".join(names) if names else tr("пока ничего")))

    def reset_layouts(self):
        if not mb_yesno("Сброс", "Удалить все сохранённые раскладки (по сессиям и по классам)? Текущие настройки останутся."):
            return
        pp = profile_path(self.game)
        data = read_json(pp)
        data.pop("layouts", None)
        try:
            write_json(pp, data)
        except OSError as e:
            mb_error("Не удалось сохранить", str(e))
            return
        self.refresh_layout_info()

    @staticmethod
    def mode_label(key, val):
        d = dict(MODES[key])
        return tr(d.get(val, next(iter(d.values()))))

    def mode_val(self, key):
        cur = self.v[key].get()
        return next((v for v, lab in MODES[key] if tr(lab) == cur), MODES[key][0][0])

    # ---------- каркас ----------
    def build_ui(self):
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, minsize=self.RW)
        self.rowconfigure(0, weight=1)
        nav = self.nav = tk.Frame(self.app.navhost, bg=UI_PANEL, width=210)
        nav.pack_propagate(False)
        tk.Label(nav, text="ПАНЕЛИ ОВЕРЛЕЯ", bg=UI_PANEL, fg=UI_DIM, font=("Segoe UI Semibold", 9),
                 anchor="w").pack(fill="x", padx=14, pady=(12, 6))

        # «Программа» закреплена внизу, список панелей над ней прокручивается — на низких экранах ничего не пропадает
        prog = tk.Frame(nav, bg=UI_PANEL)
        prog.pack(side="bottom", fill="x", pady=(0, 6))
        mid = tk.Frame(nav, bg=UI_PANEL)
        mid.pack(side="top", fill="both", expand=True)
        ncv = tk.Canvas(mid, bg=UI_PANEL, highlightthickness=0, borderwidth=0)
        nsb = ttk.Scrollbar(mid, orient="vertical", command=ncv.yview)
        ncv.configure(yscrollcommand=nsb.set)
        nsb.pack(side="right", fill="y")
        ncv.pack(side="left", fill="both", expand=True)
        inner = tk.Frame(ncv, bg=UI_PANEL)
        nwin = ncv.create_window(0, 0, window=inner, anchor="nw")
        ncv.bind("<Configure>", lambda e: ncv.itemconfigure(nwin, width=e.width))
        inner.bind("<Configure>", lambda e: ncv.configure(scrollregion=ncv.bbox("all")))
        enable_wheel(ncv, mid)
        tight = self.app.root.winfo_screenheight() < 1000
        rpad = 4 if tight else 8

        def make_row(text, cmd, dot=True, parent=None):
            row = tk.Frame(parent or inner, bg=UI_PANEL, cursor="hand2")
            row.pack(fill="x")
            strip = tk.Frame(row, bg=UI_PANEL, width=4)
            strip.pack(side="left", fill="y")
            lbl = tk.Label(row, text=tr(text), bg=UI_PANEL, fg=UI_FG, font=("Segoe UI", 11), anchor="w", padx=12, pady=rpad)
            lbl.pack(side="left", fill="x", expand=True)
            d = tk.Label(row, text="●" if dot else "", bg=UI_PANEL, fg=UI_GREEN, font=("Segoe UI", 9), padx=10)
            d.pack(side="right")
            for w in (row, lbl, d, strip):
                w.bind("<Button-1>", lambda e: cmd())
            return row, lbl, d, strip
        for key, title, _ in self.pages():
            if key != "apps":
                self.rows_ui[key] = make_row(title, lambda k=key: self.open_page(k))
        tk.Frame(prog, bg=UI_BORDER, height=1).pack(fill="x", padx=14, pady=(6, 0))
        tk.Label(prog, text="ПРОГРАММА", bg=UI_PANEL, fg=UI_DIM, font=("Segoe UI Semibold", 9),
                 anchor="w").pack(fill="x", padx=14, pady=(8, 2))
        self.rows_ui["apps"] = make_row("Доп. программы", lambda: self.open_page("apps"), parent=prog)
        self.extra_ui = {"stats": make_row("Статистика", self.app.show_stats, dot=False, parent=prog),
                         "settings": make_row("Настройки лаунчера", self.app.show_settings, dot=False, parent=prog)}

        center = ttk.Frame(self)
        center.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        center.columnconfigure(0, weight=1)
        center.rowconfigure(2, weight=1)
        self.title = ttk.Label(center, text="", style="Title.TLabel")
        self.title.grid(row=0, column=0, sticky="w")
        self.note = ttk.Label(center, text="", style="Dim.TLabel", wraplength=self.PW)
        self.note.grid(row=1, column=0, sticky="w", pady=(2, 10))
        self.canvas = TrCanvas(center, width=self.PW, height=self.PH, bg=MOCK_BG, highlightthickness=1,
                                highlightbackground=UI_BORDER)
        self.canvas.grid(row=2, column=0)                           # по центру колонки
        self.canvas.bind("<Button-1>", self.on_click)

        holder = ttk.Frame(self, width=self.RW)
        holder.grid(row=0, column=1, sticky="nsew")
        holder.grid_propagate(False)
        holder.columnconfigure(0, weight=1)
        holder.rowconfigure(0, weight=1)
        self.scroll = tk.Canvas(holder, bg=UI_BG, highlightthickness=0, borderwidth=0)
        sb = ttk.Scrollbar(holder, orient="vertical", command=self.scroll.yview)
        self.scroll.configure(yscrollcommand=sb.set)
        self.scroll.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        self.settings = ttk.Frame(self.scroll)
        self.settings.columnconfigure(0, weight=1)
        win = self.scroll.create_window(0, 0, window=self.settings, anchor="nw")
        self.scroll.bind("<Configure>", lambda e: self.scroll.itemconfigure(win, width=e.width))
        self.settings.bind("<Configure>", lambda e: self.scroll.configure(scrollregion=self.scroll.bbox("all")))
        enable_wheel(self.scroll, holder)

        bar = ttk.Frame(self)
        bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(bar, text="Сохранить", command=self.save).pack(side="left", padx=(0, 6))
        ttk.Button(bar, text="Сохранить и перезапустить оверлей", style="Accent.TButton", command=self.save_apply).pack(side="left", padx=(0, 6))
        ttk.Button(bar, text="Сбросить положение окон", command=self.reset_pos).pack(side="left")
        bar2 = ttk.Frame(self)
        bar2.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        ttk.Button(bar2, text=f"Запустить оверлей ({GAMES[self.game]})", command=lambda: self.launch("")).pack(side="left", padx=(0, 6))
        self.btn_move = ttk.Button(bar2, text="Режим перемещения окон", command=self.toggle_move)
        self.btn_move.pack(side="left", padx=(0, 6))
        ttk.Button(bar2, text="Демо без игры", command=lambda: self.launch("demo")).pack(side="left", padx=(0, 6))
        ttk.Button(bar2, text="Журнал", command=self.show_log).pack(side="left")
        self.msg = ttk.Label(self, text="", style="Dim.TLabel")
        self.msg.grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))

    # ---------- страницы ----------
    def open_page(self, key):
        self.app.select_game(self.game)
        self.show_page(key)

    def mark_view(self, view):
        """Подсветка в левом списке: страница оверлея или «Статистика» / «Настройки лаунчера»."""
        in_game = view not in ("stats", "settings")
        for k, (row, lbl, dot, strip) in self.rows_ui.items():
            sel = in_game and k == self.page
            bg = UI_FIELD if sel else UI_PANEL
            for w in (row, lbl, dot):
                w.config(bg=bg)
            strip.config(bg=UI_ACCENT if sel else UI_PANEL)
        for k, (row, lbl, dot, strip) in self.extra_ui.items():
            sel = view == k
            bg = UI_FIELD if sel else UI_PANEL
            for w in (row, lbl, dot):
                w.config(bg=bg)
            strip.config(bg=UI_ACCENT if sel else UI_PANEL)

    def show_page(self, key):
        self.page = key
        for k, (row, lbl, dot, strip) in self.rows_ui.items():
            sel = k == key
            bg = UI_FIELD if sel else UI_PANEL
            for w in (row, lbl, dot):
                w.config(bg=bg)
            strip.config(bg=UI_ACCENT if sel else UI_PANEL)
        self.mark_view(self.game)
        self.canvas.config(height=190 if key == "apps" else self.gen_h() if key == "general" else self.PH)
        self.scroll.yview_moveto(0)
        self.title.config(text=tr(next(t for k, t, _ in PAGES if k == key)))
        self.note.config(text=tr(PAGE_NOTES.get(key, "Все панели оверлея сразу. Клик по панели в предпросмотре включает или выключает её. "
                                                      "Шрифт и цвета общие для всего оверлея.")))
        for w in self.settings.winfo_children():
            w.destroy()
        self.swatches, self.value_lbls, self._cards = [], [], []
        self.build_settings(key)
        self.finish_cards(key)
        translate_tree(self.settings)
        self.refresh()

    # --- помощники: настройки раскладываются по карточкам, подписи и элементы выровнены по сетке
    def pages(self):
        return [pg for pg in PAGES if not (self.game == "lmu" and pg[0] == "lights")]

    def card(self, title, side):
        """Раздел настроек под спойлером: заголовок со знаком +/−, внутри — сетка элементов."""
        wrap = tk.Frame(side, bg=UI_BORDER)
        wrap.pack(fill="x", pady=(0, 6))
        hdr = tk.Frame(wrap, bg=UI_PANEL, cursor="hand2")
        hdr.pack(fill="x", padx=1, pady=(1, 0))
        lb = tk.Label(hdr, text=tr(title).upper(), bg=UI_PANEL, fg=UI_FG, font=("Segoe UI Semibold", 10), anchor="w",
                      padx=12, pady=9, cursor="hand2")
        lb.pack(side="left", fill="x", expand=True)
        sign = tk.Label(hdr, text="+", bg=UI_PANEL, fg=UI_DIM, font=("Segoe UI", 13), padx=12, cursor="hand2")
        sign.pack(side="right")
        f = ttk.Frame(wrap, padding=(14, 6, 14, 10))
        f.columnconfigure(0, minsize=185)
        f._r = 0
        key = (self.page, title)
        card = {"wrap": wrap, "hdr": hdr, "body": f, "sign": sign, "key": key}
        self._cards.append(card)

        def toggle(e=None, c=card):
            self._open[c["key"]] = not self._open.get(c["key"], False)
            self.apply_card(c)
        for w in (hdr, lb, sign):
            w.bind("<Button-1>", toggle)
        return f

    def apply_card(self, c):
        on = self._open.get(c["key"], False)
        c["sign"].config(text="−" if on else "+")
        if on:
            c["body"].pack(fill="x", padx=1, pady=(0, 1))
        else:
            c["body"].pack_forget()

    def finish_cards(self, page):
        """Одна карточка — без спойлера, иначе первая раскрыта, остальные свёрнуты (как их оставил пользователь)."""
        for i, c in enumerate(self._cards):
            if len(self._cards) == 1:
                c["hdr"].pack_forget()
                c["body"].pack(fill="x", padx=1, pady=1)
                continue
            self._open.setdefault(c["key"], i == 0)
            self.apply_card(c)

    @staticmethod
    def nr(parent, r=None):
        if r is None:
            r = getattr(parent, "_r", 0)
        parent._r = max(getattr(parent, "_r", 0), r + 1)
        return r

    def lab(self, parent, r, label, c=0):
        r = self.nr(parent, r)
        ttk.Label(parent, text=label).grid(row=r, column=c, sticky="w", padx=(0, 12), pady=2)
        return r

    def tg(self, parent, r, text, var, c=0, span=3, bg=None):
        r = self.nr(parent, r)
        Toggle(parent, text, var, bg=bg).grid(row=r, column=c, columnspan=span, sticky="w", pady=3, padx=(0, 16))

    def slider(self, parent, r, label, var, lo, hi, fmt):
        r = self.lab(parent, r, label)
        ttk.Scale(parent, from_=lo, to=hi, variable=var, length=150).grid(row=r, column=1, sticky="w", pady=2)
        try:
            is_size = fmt(1.5).endswith("×")
        except Exception:
            is_size = False
        if is_size:                                   # размер окна: можно вписать число руками (1.00 = обычный)
            sp = ttk.Spinbox(parent, from_=lo, to=hi, increment=0.05, textvariable=var, width=6, format="%.2f")
            sp.grid(row=r, column=2, sticky="w", padx=(8, 0))
            sp.bind("<FocusOut>", lambda e, v=var, a=lo, b=hi: self._clamp_var(v, a, b))
            sp.bind("<Return>", lambda e, v=var, a=lo, b=hi: self._clamp_var(v, a, b))
            return
        lb = ttk.Label(parent, text="", style="Dim.TLabel", width=7)
        lb.grid(row=r, column=2, sticky="w", padx=(8, 0))
        self.value_lbls.append((var, lb, fmt))

    @staticmethod
    def _clamp_var(var, lo, hi):
        try:
            v = float(str(var.get()).replace(",", "."))
        except (tk.TclError, ValueError):
            v = 1.0
        var.set(round(max(lo, min(hi, v)), 2))

    def spin(self, parent, r, label, key, lo, hi, hint=""):
        r = self.lab(parent, r, label)
        ttk.Spinbox(parent, from_=lo, to=hi, textvariable=self.v[key], width=6).grid(row=r, column=1, sticky="w", pady=2)
        if hint:
            ttk.Label(parent, text=hint, style="Dim.TLabel").grid(row=r, column=2, sticky="w", padx=(8, 0))

    def combo(self, parent, r, label, key, values, width=9):
        r = self.lab(parent, r, label)
        ttk.Combobox(parent, textvariable=self.v[key], values=values, state="readonly", width=width).grid(row=r, column=1, sticky="w", pady=2)

    def mcombo(self, parent, label, key, width=24):
        r = self.lab(parent, None, label)
        ttk.Combobox(parent, textvariable=self.v[key], values=[tr(lab) for _, lab in MODES[key]], state="readonly",
                     width=min(width, 26)).grid(row=r, column=1, columnspan=2, sticky="w", pady=2)

    def toggles(self, box, items, vars_, ncols=2):
        """Сетка переключателей внутри карточки."""
        ncols = min(ncols, 2)
        r0 = self.nr(box)
        for i, (k, name) in enumerate(items):
            Toggle(box, name, vars_[k]).grid(row=r0 + i // ncols, column=i % ncols, sticky="w", padx=(0, 22), pady=2)
        box._r = r0 + (len(items) + ncols - 1) // ncols
        for c in range(ncols):
            box.columnconfigure(c, minsize=185 if c == 0 else 0)

    def colors(self, parent, keys, start_row):
        per = len(keys)
        for i, key in enumerate(keys):
            r = start_row + i % per
            c = (i // per) * 3
            ttk.Label(parent, text=THEME_NAMES[key]).grid(row=r, column=c, sticky="w", padx=(0, 8), pady=2)
            sw = tk.Label(parent, width=4, bg=self.theme[key].get(), relief="solid", bd=1, cursor="hand2")
            sw.grid(row=r, column=c + 1, padx=(0, 6))
            sw.bind("<Button-1>", lambda e, k=key: self.pick(k))
            ttk.Button(parent, text="…", width=3, command=lambda k=key: self.pick(k)).grid(row=r, column=c + 2, padx=(0, 22))
            self.swatches.append((key, sw))
        parent._r = start_row + min(per, len(keys))

    def build_settings(self, key):
        p = self.settings
        L = ttk.Frame(p)
        L.grid(row=0, column=0, sticky="nsew", padx=(14, 6))
        R = L                                                    # все настройки — одним столбиком справа
        elem = next((e for k, _, e in PAGES if k == key), None)
        pct = lambda x: f"{x * 100:.0f}%"
        mult = lambda x: f"{x:.2f}×"
        if key == "apps":
            self.build_apps(L, R)
            return

        def main_card(title="Панель"):
            f = self.card(title, L)
            if elem:
                self.tg(f, None, "Показывать эту панель", self.v[elem])
                self.slider(f, None, "Непрозрачность", self.v["op_" + key], 0.2, 1.0, pct)
                if ("hg_" + key) in self.v:
                    self.tg(f, None, "Скрывать, когда машина в гараже", self.v["hg_" + key])
            return f

        if key == "general":
            f = self.card("Шрифт и вид", L)
            r = self.lab(f, None, "Шрифт")
            fams = ["(авто)"] + sorted({x for x in tkfont.families() if not x.startswith("@")})
            ttk.Combobox(f, textvariable=self.v["family"], values=fams, state="readonly", width=24).grid(row=r, column=1, columnspan=2, sticky="w", pady=4)
            self.spin(f, None, "Размер шрифта", "font", 8, 28)
            self.tg(f, None, "Скрывать оверлей, когда машина в гараже", self.v["hide_garage"])
            self.tg(f, None, "Записывать статистику сессий", self.v["stats"])
            self.mcombo(f, "Стиль окон", "win_style", width=34)
            self.mcombo(f, "Раскладки окон", "layout_mode", width=34)
            self.lay_info = ttk.Label(f, text="", style="Dim.TLabel", justify="left", wraplength=440)
            self.lay_info.grid(row=self.nr(f), column=0, columnspan=3, sticky="w", pady=(2, 0))
            ttk.Button(f, text="Сбросить сохранённые раскладки", command=self.reset_layouts).grid(row=self.nr(f), column=0, columnspan=3, sticky="w", pady=(6, 0))
            self.refresh_layout_info()
            els = [e for e in ELEMENTS if not (self.game == "lmu" and e[0] == "lights")]
            self.toggles(self.card("Какие панели показывать", L), els, self.v, ncols=2)
            cf = self.card("Цвета", R)
            self.colors(cf, PAGE_COLORS["general"], 0)
            ttk.Button(cf, text="Сбросить цвета", command=self.reset_colors).grid(row=6, column=0, columnspan=3, sticky="w", pady=(10, 0))
            return
        if key == "standings":
            f = main_card()
            self.slider(f, None, "Размер таблицы", self.v["scale_standings"], 0.5, 3.0, mult)
            self.mcombo(f, "Вид тайминга", "stand_mode")
            self.spin(f, None, "Строк в таблице", "rows", 4, 60)
            self.tg(f, None, "Только мой класс", self.v["class_only"])
            self.tg(f, None, "Скрывать, когда машина в гараже", self.v["hide_garage"])
            g = self.card("Бегущая строка", L)
            self.spin(g, None, "Ширина, пикселей", "ticker_w", 400, 3000)
            self.spin(g, None, "Скорость, пикселей в секунду", "ticker_speed", 20, 300)
            self.toggles(self.card("Колонки таблицы", R), [(k, n) for k, n, _ in STAND_COLS if k != "irat" or self.game == "iracing"], self.cols, ncols=3)
            self.toggles(self.card("Шапка таблицы", R), [("st_session", "Название сессии"), ("st_timer", "Таймер: круг / осталось"),
                                                         ("st_clock", "Локальное время")], self.v, ncols=1)
        elif key == "relative":
            f = main_card()
            self.slider(f, None, "Размер", self.v["scale_relative"], 0.5, 2.5, mult)
            self.spin(f, None, "Машин впереди", "rel_ahead", 0, 8)
            self.spin(f, None, "Машин позади", "rel_behind", 0, 8)
            self.toggles(self.card("Колонки релатива", R), [(k, n) for k, n in REL_COLS if k != "irat" or self.game == "iracing"], self.relc, ncols=3)
        elif key == "pedals":
            f = main_card()
            self.slider(f, None, "Размер", self.v["scale_pedals"], 0.5, 2.5, mult)
            self.spin(f, None, "График, секунд", "ped_seconds", 4, 20)
            self.tg(f, None, "Скорость в милях в час (иначе км/ч)", self.v["speed_mph"])
            self.mcombo(f, "Вид руля", "wheel_style", width=26)
            self.toggles(self.card("Что показывать", L), PED_PARTS, self.pedc, ncols=2)
            g = self.card("Шифт-лайт", R)
            self.tg(g, None, "Полоса огней по оборотам", self.v["shiftlight"])
            self.slider(g, None, "Начало", self.v["shift_start"], 50, 95, lambda x: f"{x:.0f}%")
            self.slider(g, None, "Мигание", self.v["shift_flash"], 90, 100, lambda x: f"{x:.0f}%")
        elif key == "trackmap":
            f = main_card()
            self.mcombo(f, "Вид карты", "map_mode", width=26)
            self.slider(f, None, "Размер карты", self.v["map_scale"], 0.5, 3.0, mult)
            self.spin(f, None, "Размер точек машин", "map_dot", 3, 12)
            self.combo(f, None, "Поворот карты", "map_rot", self.ROT)
            self.mcombo(f, "Стиль карты", "map_style", width=30)
            self.tg(f, None, "Рамка и заголовок вокруг карты (выкл — только сама карта)", self.v["map_frame"])
            self.tg(f, None, "Все соперники одного цвета (иначе по классам)", self.v["map_one_color"])
            h = self.card("Местная карта", L)
            self.combo(h, None, "Радиус, м", "map_radius", [str(x) for x in MAP_RADII])
            self.tg(h, None, "«Вперёд» всегда вверх (иначе север сверху)", self.v["map_headup"])
            g = self.card("На карте", R)
            self.tg(g, None, "Номера мест на машинах", self.v["map_nums"])
            self.tg(g, None, "Сектора и отметка старта/финиша", self.v["map_sectors"])
            self.tg(g, None, "Компас ветра в углу", self.v["map_wind"])
            self.tg(g, None, "Зеркально отразить карту", self.v["map_mirror"])
            if self.game == "iracing":
                self.tg(g, None, "Схема трассы с сайта iRacing (скачивается сама; нет схемы — карта по вашей траектории)", self.v["map_ir_svg"])
            ttk.Button(g, text="Стереть сохранённые карты трасс", command=self.erase_maps).grid(row=self.nr(g), column=0, columnspan=3, sticky="w", pady=(8, 0))
        elif key == "radar":
            f = main_card()
            self.slider(f, None, "Размер радара", self.v["radar_scale"], 0.5, 3.0, mult)
            self.combo(f, None, "Дальность, м", "radar_range", [str(x) for x in RADAR_RANGES])
            g = self.card("Оформление", R)
            self.tg(g, None, "Красные дуги, когда машина сбоку", self.v["radar_arcs"])
            self.tg(g, None, "Плавное проявление при приближении", self.v["radar_fade"])
        elif key == "weather":
            f = main_card()
            self.slider(f, None, "Размер", self.v["scale_weather"], 0.5, 2.5, mult)
            self.mcombo(f, "Вид", "weather_view", width=28)
            self.toggles(self.card("Карточки погоды", R), WX_CARDS, self.wxc, ncols=2)
        elif key == "lights":
            f = main_card()
            self.slider(f, None, "Размер", self.v["scale_lights"], 0.5, 2.5, mult)
            self.spin(f, None, "Зелёный горит, секунд", "lights_hold", 1, 10)
        elif key == "limiter":
            f = main_card()
            self.slider(f, None, "Размер", self.v["scale_limiter"], 0.5, 2.5, mult)
        elif key == "delta":
            f = main_card()
            self.slider(f, None, "Размер", self.v["scale_delta"], 0.5, 2.5, mult)
            self.mcombo(f, "Тема", "delta_theme", width=26)
            self.mcombo(f, "Сравнивать", "delta_ref", width=22)
            r_sb = self.nr(f)
            sb = Toggle(f, "Полоска дельты внизу", self.v["delta_sec_bar"])
            sb.grid(row=r_sb, column=0, columnspan=3, sticky="w", pady=3, padx=(0, 16))
            cws = []
            for i, (k, nm) in enumerate((("delta_show_session", "Круг «Лучший круг сессии»"), ("delta_show_mine", "Круг «Мой лучший круг»"), ("delta_show_pred", "Круг «Прогноз»"))):
                w_ = Toggle(f, nm, self.v[k])
                w_.grid(row=r_sb + 1 + i, column=0, columnspan=3, sticky="w", pady=3, padx=(0, 16))
                cws.append(w_)
            def _vis(*_a, sb=sb, cws=cws):
                t = self.mode_val("delta_theme")
                try:
                    sb.grid() if t == "sectors" else sb.grid_remove()
                    for w_ in cws:
                        w_.grid() if t not in ("full", "bar", "sectors") else w_.grid_remove()
                except tk.TclError:
                    pass
            self.v["delta_theme"].trace_add("write", _vis)
            _vis()
            f._r = r_sb + 4
            self.slider(f, None, "Шкала, секунд (±)", self.v["delta_range"], 0.3, 3.0, lambda x: f"{x:.1f} с")
        elif key in ("alerts", "tyres", "pitplan", "gaps", "engine", "sesstime", "clock", "speedo"):
            f = main_card()
            self.slider(f, None, "Размер", self.v["scale_" + key], 0.5, 2.5, mult)
            if key == "speedo":
                self.mcombo(f, "Вид", "speedo_view", width=30)
                self.spin(f, None, "Шкала циферблата, км/ч", "speedo_max", 120, 450, "км/ч")
            if key == "clock":
                self.tg(f, None, "Показывать секунды", self.v["clock_seconds"])
            if key == "tyres":
                self.mcombo(f, "Вид", "tyres_view", width=28)
            if key == "engine":
                self.mcombo(f, "Вид", "engine_view", width=28)
        elif key == "fuel":
            f = main_card()
            self.slider(f, None, "Размер окна", self.v["fuel_scale"], 0.5, 2.0, mult)
            self.combo(f, None, "Запас, кругов", "fuel_reserve", RESERVES)
            self.spin(f, None, "Средний расход за", "fuel_avg", 1, 10, "кругов")
            if self.game == "lmu":
                g = self.card("Виртуальная энергия (LMU)", R)
                self.tg(g, None, "Показывать блок энергии и её расчёт", self.v["fuel_ve"])
                ttk.Label(g, text="Остаток в %, расход за круг, среднее и сколько\nэнергии нужно до финиша — как для топлива.",
                          style="Dim.TLabel", justify="left").grid(row=self.nr(g), column=0, columnspan=3, sticky="w", pady=(2, 0))
        keys = PAGE_COLORS.get(key, [])
        if keys:
            cf = self.card("Цвета (общие для всего оверлея)", R)
            self.colors(cf, keys, 0)

    def build_apps(self, L, R):
        app = self.app
        box = self.card("Запускать вместе с оверлеем этой игры", L)
        self.app_status, self.custom_status, self.app_link = {}, {}, {}
        for i, (key, name) in enumerate(companions_for(self.game)):
            r = self.nr(box)
            Toggle(box, name + ("" if companion_runs(key, self.game) else tr(" (запускается только для iRacing)")),
                   self.v["app_" + key]).grid(row=r, column=0, columnspan=3, sticky="w", pady=(8, 0))
            r = self.nr(box)
            ttk.Entry(box, textvariable=app.app_vars[key], width=34).grid(row=r, column=0, columnspan=2, sticky="ew", pady=(2, 0))
            ttk.Button(box, text="Обзор…", command=lambda k=key: app.browse_app(k)).grid(row=r, column=2, padx=6, pady=(2, 0))
            lb = ttk.Label(box, text="", style="Dim.TLabel", wraplength=420, justify="left")
            lb.grid(row=self.nr(box), column=0, columnspan=3, sticky="w")
            self.app_status[key] = lb
            lk = tk.Label(box, text=tr("Скачать программу"), bg=UI_PANEL, fg=UI_ACCENT, cursor="hand2",
                          font=("Segoe UI", 9, "underline"), anchor="w")
            lk.grid(row=self.nr(box), column=0, columnspan=3, sticky="w", pady=(0, 2))
            lk.bind("<Button-1>", lambda e, k=key: webbrowser.open(APP_DOWNLOAD[k]))
            self.app_link[key] = lk
        cbox = self.card("Свои программы", L)
        self.custom_vars = {}
        for it in app.cfg["custom_apps"]:
            r = self.nr(cbox)
            en = tk.BooleanVar(value=self.game in it["games"])
            pv = tk.StringVar(value=it["path"])
            self.custom_vars[it["id"]] = (en, pv)
            en.trace_add("write", lambda *a, i=it["id"]: self.on_custom(i))
            pv.trace_add("write", lambda *a, i=it["id"]: self.on_custom(i))
            Toggle(cbox, it["name"], en).grid(row=r, column=0, columnspan=3, sticky="w", pady=(8, 0))
            r = self.nr(cbox)
            ttk.Entry(cbox, textvariable=pv, width=30).grid(row=r, column=0, columnspan=2, sticky="ew", pady=(2, 0))
            ttk.Button(cbox, text="Обзор…", command=lambda i=it["id"]: self.browse_custom(i)).grid(row=r, column=2, padx=6, pady=(2, 0))
            ttk.Button(cbox, text="×", width=3, command=lambda i=it["id"]: app.remove_custom(i)).grid(row=r, column=3, pady=(2, 0))
            lb = ttk.Label(cbox, text="", style="Dim.TLabel", wraplength=420, justify="left")
            lb.grid(row=self.nr(cbox), column=0, columnspan=3, sticky="w")
            self.custom_status[it["id"]] = lb
        if not app.cfg["custom_apps"]:
            ttk.Label(cbox, text="Здесь можно добавить любое приложение или ярлык — оно запустится вместе с оверлеем.",
                      style="Dim.TLabel", wraplength=420, justify="left").grid(row=self.nr(cbox), column=0, columnspan=4, sticky="w", pady=(0, 4))
        ttk.Button(cbox, text="+ Добавить программу…", style="Accent.TButton", command=app.add_custom).grid(
            row=self.nr(cbox), column=0, sticky="w", pady=(10, 0))
        obox = self.card("Параметры", L)
        Toggle(obox, "Закрывать эти программы вместе с оверлеем", app.apps_close_var).grid(
            row=self.nr(obox), column=0, columnspan=4, sticky="w", pady=4)
        ttk.Label(obox, text="Путь можно не указывать, если программа стоит в обычном месте — лаунчер найдёт её сам. "
                  "Уже запущенные программы повторно не открываются.", style="Dim.TLabel", wraplength=440, justify="left").grid(
            row=self.nr(obox), column=0, columnspan=4, sticky="w", pady=(4, 0))
        self.update_app_status()

    def on_custom(self, cid):
        it = next((c for c in self.app.cfg["custom_apps"] if c["id"] == cid), None)
        pair = self.custom_vars.get(cid)
        if not it or not pair:
            return
        en, pv = pair
        try:
            it["path"] = pv.get().strip()
            games = set(it["games"])
            (games.add if en.get() else games.discard)(self.game)
            it["games"] = sorted(games)
        except tk.TclError:
            return
        self.app.save_cfg()
        self.update_app_status()
        self.changed()

    def browse_custom(self, cid):
        path = filedialog.askopenfilename(parent=self.app.root, title=tr("Выберите программу"),
                                          filetypes=[(tr("Программы и ярлыки"), "*.exe *.lnk *.bat *.cmd"), (tr("Все файлы"), "*.*")])
        if path and cid in self.custom_vars:
            self.custom_vars[cid][1].set(os.path.normpath(path))

    def update_app_status(self):
        for key, lb in getattr(self, "app_status", {}).items():
            try:
                path = resolve_app(key, self.app.cfg)
                typed = self.app.app_vars[key].get().strip()
                lk = getattr(self, "app_link", {}).get(key)
                if lk is not None:
                    lk.grid() if not path else lk.grid_remove()          # «Скачать программу» — только если её нет
                if path:
                    lb.config(text=tr("Найдена: ") + path, foreground=UI_GREEN_DK if UI_THEME_NAME == "light" else UI_GREEN)
                    lb.grid()
                elif typed:
                    lb.config(text=tr("Файл не найден — проверьте путь"), foreground="#ff5a4f")
                    lb.grid()
                else:
                    lb.grid_remove()                        # не найдена и путь не задан — подсказка есть в предпросмотре
            except tk.TclError:
                pass
        for cid, lb in getattr(self, "custom_status", {}).items():
            try:
                it = next((c for c in self.app.cfg["custom_apps"] if c["id"] == cid), None)
                ok = bool(it and resolve_app(cid, self.app.cfg))
                lb.config(text=tr("Найдена: ") + it["path"] if ok else tr("Файл не найден — проверьте путь"),
                          foreground=(UI_GREEN_DK if UI_THEME_NAME == "light" else UI_GREEN) if ok else "#ff5a4f")
            except tk.TclError:
                pass

    # ---------- данные ----------
    def num(self, key, default, cast=float):
        try:
            return cast(self.v[key].get())
        except (tk.TclError, ValueError):
            return default

    def collect(self):
        st = {}
        lim = lambda x, lo, hi: max(lo, min(hi, x))
        for k, dv in DEFAULTS.items():
            if k == "family":
                fam = self.v[k].get()
                st[k] = "" if fam in ("", "(авто)") else fam
            elif k == "map_rot":
                val = self.v[k].get()
                st[k] = self.ROT.index(val) if val in self.ROT else 0
            elif k in MODES:
                st[k] = self.mode_val(k)
            elif k in ("radar_range", "map_radius"):
                st[k] = self.num(k, dv, int)
            elif isinstance(dv, bool):
                st[k] = bool(self.v[k].get())
            elif isinstance(dv, int):
                st[k] = self.num(k, dv, int)
            else:
                st[k] = round(self.num(k, dv), 3)
        st["font"] = lim(st["font"], 8, 28)
        st["rows"] = lim(st["rows"], 4, 60)
        for k in ("rel_ahead", "rel_behind"):
            st[k] = lim(st[k], 0, 8)
        st["ped_seconds"] = lim(st["ped_seconds"], 4, 20)
        st["shift_start"] = lim(st["shift_start"], 50, 95)
        st["shift_flash"] = lim(st["shift_flash"], st["shift_start"] + 2, 100)
        st["map_dot"] = lim(st["map_dot"], 3, 12)
        st["fuel_avg"] = lim(st["fuel_avg"], 1, 10)
        st["ticker_w"] = lim(st["ticker_w"], 400, 3000)
        st["ticker_speed"] = lim(st["ticker_speed"], 20, 300)
        st["map_radius"] = lim(st["map_radius"], 100, 3000)
        st["delta_range"] = round(lim(st["delta_range"], 0.3, 5.0), 2)
        st["speedo_max"] = lim(st["speedo_max"], 120, 450)
        st["lights_hold"] = lim(st["lights_hold"], 1, 10)
        for k in SCALE_DEFAULT:
            st[k] = round(lim(st[k], 0.5, 3.0), 2)
        for k in OP_DEFAULT:
            st["op_" + k] = round(lim(st["op_" + k], 0.2, 1.0), 2)
        st["fuel_reserve"] = self.num("fuel_reserve", 0.0)
        st["hide_cols"] = [k for k, v in self.cols.items() if not v.get()]
        st["rel_hide"] = [k for k, v in self.relc.items() if not v.get()]
        st["wx_hide"] = [k for k, v in self.wxc.items() if not v.get()]
        st["ped_hide"] = [k for k, v in self.pedc.items() if not v.get()]
        st["theme"] = {k: (v.get() if valid_hex(v.get()) else THEME_DEFAULT[k]) for k, v in self.theme.items()}
        if self.game == "lmu":
            st["lights"] = False
        return st

    def save(self, quiet=False):
        try:
            save_settings(self.game, self.collect())
        except OSError as e:
            mb_error("Не удалось сохранить", str(e))
            return False
        if not quiet:
            self.say(f"Сохранено: {profile_path(self.game)}")
        return True

    def save_apply(self):
        if not self.save(quiet=True):
            return
        r = self.app.runner
        if r.running() and r.game == self.game and r.mode != "demo":
            r.start(self.game, r.mode, r.auto)
            self.say("Сохранено, оверлей перезапущен.")
        else:
            self.say("Сохранено. Изменения применятся при следующем запуске оверлея.")
        self.app.refresh_status()

    def launch(self, mode):
        if not self.save(quiet=True):
            return
        if not os.path.isfile(OVERLAY):
            mb_error("Нет файла", f"Рядом с лаунчером не найден {os.path.basename(OVERLAY)}")
            return
        self.app.runner.start(self.game, mode, auto=False)
        self.say({"": "Оверлей запущен.", "unlock": "Режим перемещения: двигайте окна мышью, затем Ctrl+Shift+L — закрепить.",
                  "demo": "Демо без игры (закрыть: Ctrl+Shift+Q или «Остановить оверлей»)."}[mode])
        self.app.refresh_status()

    def move_active(self):
        """Включён ли сейчас режим перемещения у работающего оверлея этой игры."""
        r = self.app.runner
        if not (r.running() and r.game == self.game and r.mode != "demo"):
            return False
        locked = read_locked(self.game)
        return (locked is False) if locked is not None else (r.mode == "unlock")

    def toggle_move(self):
        """Первое нажатие включает режим перемещения, второе — закрепляет окна и выходит из него."""
        r = self.app.runner
        if r.running() and r.game == self.game and r.mode != "demo":
            on = self.move_active()
            if send_cmd(self.game, "lock" if on else "unlock"):
                self.say("Окна закреплены, режим перемещения выключен." if on else
                         "Режим перемещения: двигайте окна мышью, затем нажмите эту кнопку ещё раз.")
                self.set_move_btn(not on)
            else:
                mb_error("Не удалось", "Не получилось передать команду оверлею.")
        else:
            self.launch("unlock")
            self.set_move_btn(True)

    def set_move_btn(self, on):
        try:
            self.btn_move.config(style="Active.TButton" if on else "TButton",
                                 text=tr("✔ Режим перемещения ВКЛ — нажмите, чтобы закрепить") if on else tr("Режим перемещения окон"))
        except tk.TclError:
            pass

    def reset_colors(self):
        for k, v in THEME_DEFAULT.items():
            self.theme[k].set(v)

    def reset_pos(self):
        if mb_yesno("Положение окон", "Вернуть все окна оверлея на стандартные места?"):
            reset_positions(self.game)
            self.say("Положение сброшено — применится при следующем запуске.")

    def erase_maps(self):
        if not mb_yesno("Карты трасс", "Стереть все сохранённые карты трасс?\nОни нарисуются заново за пару кругов."):
            return
        n = 0
        try:
            for name in os.listdir(MAP_DIR):
                if name.lower().endswith(".json"):
                    os.remove(os.path.join(MAP_DIR, name))
                    n += 1
        except OSError:
            pass
        self.say(f"Стёрто карт: {n}.")

    def show_log(self):
        path = os.path.join(LOGS, f"overlay-{self.game}.log")
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()[-4000:] or "(журнал пуст)"
        except OSError:
            text = "Журнала пока нет — оверлей ещё не запускался."
        mb_info("Журнал оверлея", text)

    def say(self, text):
        self.msg.config(text=tr(text))

    def pick(self, key):
        rgb, hexv = colorchooser.askcolor(color=self.theme[key].get(), parent=self, title="Выбор цвета")
        if hexv:
            self.theme[key].set(hexv)

    # ---------- обновление ----------
    def changed(self):
        if not self._pending:
            self._pending = True
            self.after(50, self.refresh)

    def refresh(self):
        self._pending = False
        for key, sw in self.swatches:
            try:
                sw.config(bg=self.theme[key].get())
            except tk.TclError:
                pass
        for var, lb, fmt in self.value_lbls:
            try:
                lb.config(text=fmt(float(var.get())))
            except (tk.TclError, ValueError):
                pass
        if self.page == "apps":
            self.update_app_status()
        row, lbl, dot, strip = self.rows_ui["apps"]
        anyon = any(self.v["app_" + k].get() for k, _ in companions_for(self.game) if companion_runs(k, self.game)) or any(self.game in c["games"] for c in self.app.cfg["custom_apps"])
        dot.config(text="●" if anyon else "○", fg=UI_GREEN if anyon else UI_DIM)
        for key, _, elem in self.pages():
            if elem is None:
                continue
            row, lbl, dot, strip = self.rows_ui[key]
            on = bool(self.v[elem].get())
            dot.config(text="●" if on else "○", fg=UI_GREEN if on else UI_DIM)
            lbl.config(fg=UI_FG if on else UI_DIM)
        self.draw_preview()

    def on_click(self, e):
        if self.page != "general":
            return
        for key, x0, y0, x1, y1 in self.hits:
            if x0 <= e.x <= x1 and y0 <= e.y <= y1:
                self.v[key].set(not self.v[key].get())
                return

    # ---------- предпросмотр ----------
    OV_H = 640                                          # высота общего предпросмотра в натуральную величину

    def gen_h(self):
        wh = getattr(self.app, "win_h", 1000)
        return int(max(330, min(self.GH, wh - 330)))

    base_k = 1.0

    def make_fonts(self, k):
        k = k * self.base_k
        fam = self.v["family"].get()
        fam = "Segoe UI" if fam in ("", "(авто)") else fam
        fs = int(self.num("font", 13))
        mk = lambda size, bold: tkfont.Font(family=fam, size=max(5, int(round(size * k))), weight="bold" if bold else "normal")
        self.f_s = mk(max(6, fs // 2), True)
        self.f_t = mk(max(6, fs // 2 - 1), False)
        self.f_b = mk(max(9, fs * 0.9), True)

    def compute_theme(self, key="standings"):
        op = max(0.2, min(1.0, self.num("op_" + key, 0.94))) if key in OP_DEFAULT else 0.94
        th = {}
        for k, v in self.theme.items():
            c = v.get()
            c = c if valid_hex(c) else THEME_DEFAULT[k]
            th[k] = mix(MOCK_BG, c, op) if k in ("panel", "background", "border") else c
        self.th = th

    def draw_preview(self):
        c = self.canvas
        c.delete("all")
        self.hits = []
        ph = int(float(c.cget("height")))
        for i in range(0, self.PW, 40):                 # «трасса» на заднем плане
            c.create_line(i, 0, i + 120, ph, fill="#222a39")
        if self.page == "general":
            kk = min(1.0, ph / self.OV_H)
            self.base_k = kk
            self.make_fonts(1.0)
            self.draw_overview()
            if kk < 1.0:
                for item in c.find_all():
                    c.scale(item, 0, 0, kk, kk)
                    c.move(item, (self.PW - 680 * kk) / 2, 0)
                ox = (self.PW - 680 * kk) / 2
                self.hits = [(k_, x0 * kk + ox, y0 * kk, x1 * kk + ox, y1 * kk) for k_, x0, y0, x1, y1 in self.hits]
            self.base_k = 1.0
        else:
            self.compute_theme(self.page)
            self.draw_single(self.page)

    def stand_cols(self):
        """Колонки таблицы в порядке оверлея: (ключ, ширина) только включённые."""
        w = {k: sw for k, _, sw in STAND_COLS}
        out = [("pos", 18)]
        for k in ("delta", "logo"):
            if self.cols[k].get():
                out.append((k, w[k]))
        out.append(("num", 20))
        if self.cols["tyre"].get():
            out.append(("tyre", w["tyre"]))
        out.append(("name", 84))
        if self.game == "iracing" and self.cols["irat"].get():
            out.append(("irat", w["irat"]))
        for k in ("ahead", "lead", "best", "last", "fuel", "ve", "status"):
            if self.cols[k].get():
                out.append((k, w[k]))
        return out

    def draw_overview(self):
        c = self.canvas
        n = max(4, min(8, int(self.num("rows", 12))))
        ra, rb = int(self.num("rel_ahead", 3)), int(self.num("rel_behind", 3))
        rel_h = 30 + (ra + 1 + rb) * 12
        sh = 34 + n * 20
        layout = [
            ("standings", "Таблица", 10, 10, 220, sh, self.d_standings),
            ("relative", "Релатив", 240, 10, 140, rel_h, self.d_relative),
            ("radar", "Радар", 390, 10, 110, 110, self.d_radar),
            ("weather", "Погода", 510, 10, 160, 150, self.d_weather),
            ("pedals", "Ввод", 240, 20 + rel_h, 260, 66, self.d_pedals),
            ("trackmap", "Карта", 240, 96 + rel_h, 150, 150, self.d_map),
            ("fuel", "Топливо", 400, 96 + rel_h, 100, 170, self.d_fuel),
            ("delta", "Дельта", 510, 170, 160, {"bar": 38, "sectors": 152 if self.v["delta_sec_bar"].get() else 112}.get(self.mode_val("delta_theme"), 100) if self.mode_val("delta_theme") in ("full", "bar", "sectors") else self.delta_th_h(), self.d_delta),
            ("lights", "Огни", 10, sh + 20, 200, 50, self.d_lights),
            ("limiter", "Лимитер", 10, sh + (80 if self.game != "lmu" else 20), 150, 40, self.d_limiter),
        ]
        if self.game == "lmu":
            layout = [t for t in layout if t[0] != "lights"]
        ph = max(int(float(c.cget("height"))), self.OV_H)
        for key, label, x, y, w, h, fn in layout:
            if y + h > ph - 4:
                h = max(30, ph - y - 4)
            self.compute_theme(key)
            if self.v[key].get():
                fn(x, y, w, h)
            else:
                c.create_rectangle(x, y, x + w, y + h, outline="#4a5260", dash=(4, 4))
                c.create_text(x + w / 2, y + h / 2, text=f"{label}\nвыключено", fill="#5d6675", font=self.f_t, justify="center")
            self.hits.append((key, x, y, x + w, y + h))
        # нижние ряды: остальные панели рисуются в натуральную величину и уменьшаются целиком
        yb = max(t[3] + t[5] for t in layout) + 12
        nat = {"tyres": {"compact": (150, 190), "wear": (260, 92)}.get(self.mode_val("tyres_view"), (200, 160)), "pitplan": (260, 150),
               "gaps": (270, 130), "engine": {"compact": (360, 34), "gauges": (170, 200)}.get(self.mode_val("engine_view"), (230, 200)),
               "alerts": (240, 120), "sesstime": (190, 52), "clock": (170, 52), "speedo": self.speedo_size()}
        fns = {"tyres": self.d_tyres, "pitplan": self.d_pitplan, "gaps": self.d_gaps, "engine": self.d_engine, "alerts": self.d_alerts,
               "sesstime": self.d_sesstime, "clock": self.d_clock, "speedo": self.d_speedo}
        rows = [(("tyres", "pitplan", "gaps", "engine"), 0.656), (("alerts", "sesstime", "clock", "speedo"), 0.62)]
        for keys, k in rows:
            x = 10
            rh_ = 0
            for key in keys:
                dw, dh = nat[key]
                self.compute_theme(key)
                self.make_fonts(k)
                before = set(c.find_all())
                if self.v[key].get():
                    fns[key](0, 0, dw, dh)
                else:
                    c.create_rectangle(0, 0, dw, dh, outline="#4a5260", dash=(4, 4))
                    c.create_text(dw / 2, dh / 2, text="выключено", fill="#5d6675", font=self.f_t)
                for item in [i for i in c.find_all() if i not in before]:
                    c.scale(item, 0, 0, k, k)
                    c.move(item, x, yb)
                self.hits.append((key, x, yb, x + dw * k, yb + dh * k))
                x += dw * k + 10
                rh_ = max(rh_, dh * k)
            yb += rh_ + 10
        self.make_fonts(1.0)
        c.create_text(self.PW - 8, ph - 8, text="клик по панели — включить / выключить", fill="#5d6675", anchor="se", font=self.f_t)

    def draw_single(self, key):
        c = self.canvas
        ph = 190 if key == "apps" else self.PH
        n = max(4, min(14, int(self.num("rows", 12))))
        count = int(self.num("rel_ahead", 3)) + 1 + int(self.num("rel_behind", 3))
        ncards = max(1, sum(1 for v in self.wxc.values() if v.get()))
        sw = 8 + sum(w for _, w in self.stand_cols()) + 8
        smode = self.mode_val("stand_mode")
        if smode == "ticker":
            stand_size = (max(sw, 520), 40)
        elif smode == "compact":
            stand_size = (sw, 34 + 8 * 22)
        else:
            stand_size = (sw, 34 + n * 22)
        mmode = self.mode_val("map_mode")
        map_size = {"full": (180, 190), "linear": (320, 150), "local": (190, 200)}[mmode]
        sizes = {"delta": ((190, self.delta_th_h()) if self.mode_val("delta_theme") not in ("full", "bar", "sectors") else {"bar": (190, 38), "sectors": (190, 152 if self.v["delta_sec_bar"].get() else 112)}.get(self.mode_val("delta_theme"), (190, 124))), "alerts": (240, 120),
                 "tyres": {"compact": (150, 190), "wear": (260, 92)}.get(self.mode_val("tyres_view"), (200, 160)), "pitplan": (260, 150), "gaps": (270, 130), "sesstime": (190, 52), "clock": (170, 52), "speedo": self.speedo_size(),
                 "engine": {"compact": (360, 34), "gauges": (170, 200)}.get(self.mode_val("engine_view"), (230, 200)),
                 "standings": stand_size, "relative": (176 + (84 if self.game == "iracing" and self.relc["irat"].get() else 0), 26 + count * 18), "pedals": (290, 76),
                 "trackmap": map_size, "radar": (130, 130), "weather": ((70 * ncards + 10, 56) if self.mode_val("weather_view") == "compact" else (180, 6 + ncards * 46)),
                 "lights": (210, 58), "limiter": (160, 46),
                 "fuel": (140, 232 if (self.game == "lmu" and self.v["fuel_ve"].get()) else 176),
                 "apps": (320, 36 + 26 * (len(companions_for(self.game)) + len(self.app.cfg["custom_apps"])))}
        fn = {"apps": self.d_apps, "standings": self.d_standings, "relative": self.d_relative, "pedals": self.d_pedals,
              "trackmap": self.d_map, "radar": self.d_radar, "weather": self.d_weather, "lights": self.d_lights,
              "limiter": self.d_limiter, "fuel": self.d_fuel, "delta": self.d_delta, "alerts": self.d_alerts,
              "tyres": self.d_tyres, "pitplan": self.d_pitplan, "gaps": self.d_gaps, "engine": self.d_engine,
              "sesstime": self.d_sesstime, "clock": self.d_clock, "speedo": self.d_speedo}[key]
        dw, dh = sizes[key]
        user = 1.0
        if key in SCALE_KEYS:
            user = self.num(SCALE_KEYS[key], 1.0) / (0.8 if key == "fuel" else 1.0)
        fit = min(self.PW * 0.92 / dw, ph * 0.92 / dh)
        k = max(0.4, min(fit, 3.2, fit * 0.85 * user))
        self.make_fonts(k)
        before = set(c.find_all())
        if key == "apps" or self.v[key].get():
            fn(0, 0, dw, dh)
        else:
            c.create_rectangle(0, 0, dw, dh, outline="#4a5260", dash=(4, 4))
            c.create_text(dw / 2, dh / 2, text="выключено", fill="#5d6675", font=self.f_t)
        for item in [i for i in c.find_all() if i not in before]:
            c.scale(item, 0, 0, k, k)
            c.move(item, (self.PW - dw * k) / 2, (ph - dh * k) / 2)

    STYLE_R = {"classic": 1.0, "square": 0.0, "soft": 1.9, "minimal": 0.6, "accent": 1.0}

    def mark(self, c, x0, y0, x1, y1):
        """Цветная метка у заголовка панели: зависит от стиля окон."""
        st = self.mode_val("win_style")
        if st != "minimal":
            c.create_rectangle(x0, y0, x1, y1, fill=self.th["accent"] if st == "accent" else "#e5322d", outline="")

    def panel(self, x, y, w, h, fill=None, r=10):
        st = self.mode_val("win_style")
        r = max(0, min(int(r * self.STYLE_R.get(st, 1.0)), h // 2, w // 2))
        t = self.th["panel"] if fill is None else fill
        pts = [x + r, y, x + w - r, y, x + w, y, x + w, y + r, x + w, y + h - r, x + w, y + h,
               x + w - r, y + h, x + r, y + h, x, y + h, x, y + h - r, x, y + r, x, y]
        if r:
            self.canvas.create_polygon(pts, smooth=True, fill=t, outline=self.th["accent"] if st == "accent" else self.th["border"])
        else:
            self.canvas.create_rectangle(x, y, x + w, y + h, fill=t, outline=self.th["accent"] if st == "accent" else self.th["border"])

    def d_standings(self, x, y, w, h):
        c, th = self.canvas, self.th
        bg = th["background"]
        self.panel(x, y, w, h, fill=bg)
        self.mark(c, x + 6, y + 8, x + 9, y + 24)
        c.create_text(x + 16, y + 16, text={"lmu": "LMU", "rf2": "rF2"}.get(self.game, "iR"), fill=th["text"], anchor="w", font=self.f_b)
        if self.v["st_timer"].get():
            c.create_text(x + w - 8, y + 16, text="02:41:07", fill=th["accent"], anchor="e", font=self.f_b)
        smode = self.mode_val("stand_mode")
        if smode == "ticker":
            self.d_ticker(x, y, w, h)
            return
        n = max(4, min(14, int(self.num("rows", 12))))
        idxs = [0, 1, 2, 5, 6, 7, 8, 9] if smode == "compact" else list(range(n))
        if smode == "compact":
            n = len(idxs)
        rh = (h - 34) / n
        cols, xs, cx = self.stand_cols(), {}, x + 8
        for k, cw in cols:
            if cx + cw > x + w - 6 and k != "name":
                continue
            xs[k] = (cx, cw)
            cx += cw
        tyres = ["#ffd23f", "#ffffff", "#ff4d4d"]
        for r_i in range(n):
            i = idxs[r_i]
            name = self.NAMES[i % len(self.NAMES)]
            yy = y + 30 + r_i * rh
            mid = yy + rh / 2
            me = i == (7 if smode == "compact" else 3)
            fill = mix(bg, th["me"], 0.30) if me else mix(bg, "#ffffff", 0.035 if i % 2 == 0 else 0.015)
            c.create_rectangle(x + 4, yy, x + w - 4, yy + rh - 1, fill=fill, outline=th["me"] if me else "")
            for k, (kx, cw) in xs.items():
                if k == "pos":
                    c.create_text(kx + cw - 4, mid, text=str(i + 1), fill=th["text"], anchor="e", font=self.f_t)
                elif k == "delta":
                    d = (0, 1, -1, 0, 2, 0, -1)[i % 7]
                    if d:
                        c.create_text(kx + 2, mid, text=("▲" if d > 0 else "▼") + str(abs(d)), fill="#2ee26d" if d > 0 else "#ff5a4f", anchor="w", font=self.f_t)
                elif k == "logo":
                    c.create_oval(kx + 2, mid - 5, kx + 12, mid + 5, fill=self.CLS[i % len(self.CLS)], outline="")
                elif k == "num":
                    c.create_rectangle(kx, yy + 2, kx + cw - 4, yy + rh - 3, fill=mix(bg, "#ffffff", 0.10), outline="")
                    c.create_rectangle(kx, yy + 3, kx + 2, yy + rh - 4, fill=self.CLS[i % len(self.CLS)], outline="")
                    c.create_text(kx + cw / 2 - 1, mid, text=str(7 + i * 3), fill=th["text"], font=self.f_t)
                elif k == "tyre":
                    c.create_oval(kx + 3, mid - 5, kx + 13, mid + 5, outline=tyres[i % 3], width=2)
                elif k == "name":
                    c.create_text(kx + 2, mid, text=name, fill=th["text"], anchor="w", font=self.f_t)
                elif k == "irat":
                    self.irat_chips(kx + cw - 2, mid, i, rh)
                elif k in ("ahead", "lead"):
                    c.create_text(kx + cw - 2, mid, text="+%.1f" % (i * (3.7 if k == "lead" else 1.1)), fill=th["dim"], anchor="e", font=self.f_t)
                elif k in ("best", "last"):
                    c.create_text(kx + cw - 2, mid, text="1:41.%03d" % (230 + i * 17 + (40 if k == "last" else 0)), fill=th["dim"], anchor="e", font=self.f_t)
                elif k == "fuel":
                    c.create_text(kx + cw - 2, mid, text="%d%%" % (82 - i * 4), fill=th["dim"], anchor="e", font=self.f_t)
                elif k == "status":
                    if i in (2, 5):
                        c.create_text(kx + cw - 2, mid, text="1 PIT" if i == 2 else "PIT", fill=th["dim"], anchor="e", font=self.f_t)
                elif k == "ve":
                    c.create_rectangle(kx + 2, mid - 2, kx + cw - 4, mid + 2, fill="#262d38", outline="")
                    c.create_rectangle(kx + 2, mid - 2, kx + 2 + (cw - 6) * (0.9 - i * 0.05), mid + 2, fill="#4a90ff", outline="")

    LIC = (("A 4.12", "#0153db", "#ffffff"), ("B 3.40", "#00c702", "#ffffff"), ("C 2.75", "#feec04", "#10151c"),
           ("D 2.10", "#fc8a27", "#ffffff"), ("R 0.95", "#fc0706", "#ffffff"))

    def irat_chips(self, right, mid, i, rh):
        """Плашки как в iRacing: лицензия в своём цвете и светлая плашка с iRating."""
        c = self.canvas
        hh = max(5, rh * 0.30)
        t = "%.1fk" % (1.2 + (i * 0.7) % 5)
        w = len(t) * 5 + 6
        c.create_rectangle(right - w, mid - hh, right, mid + hh, fill="#e9edf2", outline="")
        c.create_text(right - w / 2, mid, text=t, fill="#10151c", font=self.f_t)
        lic, bg, fg = self.LIC[i % len(self.LIC)]
        x = right - w - 4
        w2 = len(lic) * 5 + 6
        c.create_rectangle(x - w2, mid - hh, x, mid + hh, fill=bg, outline="")
        c.create_text(x - w2 / 2, mid, text=lic, fill=fg, font=self.f_t)

    def d_ticker(self, x, y, w, h):
        c, th = self.canvas, self.th
        self.panel(x, y, w, h, fill=th["background"], r=8)
        capw = 66
        c.create_text(x + 8, y + h / 2, text="P4/14", fill=th["text"], anchor="w", font=self.f_s)
        c.create_line(x + capw, y + 5, x + capw, y + h - 5, fill=th["accent"])
        xx = x + capw + 12
        for i in range(12):
            if xx > x + w - 90:
                break
            col = self.CLS[i % len(self.CLS)]
            me = i == 3
            if me:
                c.create_rectangle(xx - 4, y + 4, xx + 112, y + h - 4, fill=mix(th["background"], th["me"], 0.3), outline=th["me"])
            c.create_rectangle(xx, y + 7, xx + 3, y + h - 7, fill=col, outline="")
            c.create_text(xx + 8, y + h / 2, text=f"{i + 1}  #{7 + i * 3}  {self.NAMES[i % len(self.NAMES)][:9]}  +{i * 1.1:.1f}",
                          fill=th["text"] if me else th["dim"], anchor="w", font=self.f_t)
            xx += 128
        c.create_text(x + w - 6, y + h - 5, text="→ едет справа налево", fill=th["dim"], anchor="se", font=self.f_t)

    def d_relative(self, x, y, w, h):
        c, th = self.canvas, self.th
        self.panel(x, y, w, h, fill=th["background"])
        c.create_text(x + 10, y + 12, text="RELATIVE", fill=th["accent"], anchor="w", font=self.f_t)
        ra, rb = int(self.num("rel_ahead", 3)), int(self.num("rel_behind", 3))
        rows = [(f"-{i}", f"-{i * 1.4:.1f}") for i in range(ra, 0, -1)] + [("ВЫ", "")] + \
               [(f"+{i}", f"+{i * 1.5:.1f}") for i in range(1, rb + 1)]
        rh = (h - 24) / len(rows)
        show_pos, show_num, show_tag = (self.relc[k].get() for k in ("pos", "num", "tag"))
        for i, (p, g) in enumerate(rows):
            yy = y + 22 + i * rh
            mid = yy + rh / 2
            if p == "ВЫ":
                c.create_rectangle(x + 4, yy, x + w - 4, yy + rh - 1, fill=mix(th["background"], th["me"], 0.3), outline=th["me"])
            cx = x + 8
            if show_pos:
                c.create_text(cx, mid, text=p, fill=th["text"], anchor="w", font=self.f_t)
                cx += 22
            if show_num:
                c.create_rectangle(cx, yy + 2, cx + 18, yy + rh - 3, fill=mix(th["background"], "#ffffff", 0.10), outline="")
                c.create_text(cx + 9, mid, text=str(10 + i * 4), fill=th["text"], font=self.f_t)
                cx += 22
            c.create_text(cx, mid, text=self.NAMES[(i + 1) % len(self.NAMES)], fill=th["text"], anchor="w", font=self.f_t)
            gx = x + w - (30 if show_tag else 8)
            if self.game == "iracing" and self.relc["irat"].get() and w >= 220:
                self.irat_chips(gx - 26, mid, i, rh)
            c.create_text(gx, mid, text=g, fill=th["text"], anchor="e", font=self.f_t)
            if show_tag and i == ra + 2:
                c.create_text(x + w - 8, mid, text="PIT", fill=th["accent"], anchor="e", font=self.f_t)

    def d_radar(self, x, y, w, h):
        c, th = self.canvas, self.th
        cx, cy, r = x + w / 2, y + h / 2, min(w, h) / 2 - 3
        c.create_oval(cx - r, cy - r, cx + r, cy + r, outline="#7c8696", width=2)
        for f in (0.33, 0.66):
            c.create_oval(cx - r * f, cy - r * f, cx + r * f, cy + r * f, outline="#3b4350", dash=(2, 4))
        c.create_line(cx - r, cy, cx + r, cy, fill="#3b4350", dash=(2, 4))
        c.create_line(cx, cy - r, cx, cy + r, fill="#3b4350", dash=(2, 4))
        c.create_rectangle(cx - 4, cy - 9, cx + 4, cy + 9, fill="#ffffff", outline=th["accent"])
        c.create_rectangle(cx - 22, cy - 26, cx - 14, cy - 8, fill="#ff3b30", outline="#0d1117")
        c.create_rectangle(cx + 16, cy + 30, cx + 24, cy + 48, fill="#2ee26d", outline="#0d1117")
        if self.v["radar_arcs"].get():
            c.create_arc(cx - r, cy - r, cx + r, cy + r, start=142, extent=76, style="arc", outline="#ff3b30", width=4)
        c.create_text(cx, y + h - 4, text=f"{self.v['radar_range'].get()} м", fill=th["dim"], font=self.f_t)

    def d_weather(self, x, y, w, h):
        c, th = self.canvas, self.th
        card = mix(th["panel"], "#000000", 0.12)
        allc = {"track": ("TRACK TEMP", "34 °C"), "air": ("AIR TEMP", "22 °C"), "wet": ("TRACK WETNESS", "lightly wet"),
                "rain": ("PRECIPITATION", "4 %")}
        on = [k for k, _ in WX_CARDS if self.wxc[k].get()] or ["track"]
        if self.mode_val("weather_view") == "compact":
            short = {"track": ("ТРАССА", "34°"), "air": ("ВОЗДУХ", "22°"), "wet": ("ВЛАЖН.", "лёгк."), "rain": ("ДОЖДЬ", "4%")}
            cw_ = (w - (len(on) + 1) * 6) / len(on)
            for i, k in enumerate(on):
                xx = x + 6 + i * (cw_ + 6)
                self.panel(xx, y + 6, cw_, h - 12, fill=card, r=6)
                c.create_text(xx + cw_ / 2, y + 17, text=short[k][0], fill=th["dim"], font=self.f_t)
                c.create_text(xx + cw_ / 2, y + h - 18, text=short[k][1], fill=th["text"], font=self.f_s)
            return
        ch = (h - (len(on) + 1) * 6) / len(on)
        for i, k in enumerate(on):
            tt, val = allc[k]
            yy = y + 6 + i * (ch + 6)
            self.panel(x, yy, w, ch, fill=card, r=6)
            c.create_text(x + 8, yy + 9, text=tt, fill=th["dim"], anchor="w", font=self.f_t)
            c.create_text(x + 8, yy + ch - 10, text=val, fill=th["text"], anchor="w", font=self.f_s)
            if k == "wet":
                c.create_rectangle(x + w - 60, yy + ch - 14, x + w - 8, yy + ch - 8, fill="#262d38", outline="")
                c.create_rectangle(x + w - 60, yy + ch - 14, x + w - 38, yy + ch - 8, fill="#3aa0ff", outline="")

    def d_pedals(self, x, y, w, h):
        c, th = self.canvas, self.th
        self.panel(x, y, w, h)
        top = y + 4
        if self.v["shiftlight"].get():
            n = 15
            s0 = self.num("shift_start", 82.0) / 100.0
            f0 = max(self.num("shift_flash", 98.0) / 100.0, s0 + 0.02)
            frac = s0 + (f0 - s0) * 0.78                       # пример: обороты почти у границы мигания
            lw = (w - 16 - 2 * (n - 1)) / n
            for i in range(n):
                col = "#2ee26d" if i < 5 else "#ff3b30" if i < 10 else "#4a90ff"
                lit = frac >= s0 + (f0 - 0.005 - s0) * i / (n - 1)
                c.create_rectangle(x + 8 + i * (lw + 2), y + 5, x + 8 + i * (lw + 2) + lw, y + 11,
                                   fill=col if lit else "#262d38", outline="")
            top = y + 14
        mid = (top + y + h) / 2
        mph = bool(self.v["speed_mph"].get())
        show = {k: v.get() for k, v in self.pedc.items()}
        if show["gear"]:
            c.create_text(x + 14, mid, text="4", fill=th["accent"], anchor="w", font=self.f_b)
        if show["speed"]:
            c.create_text(x + 40, mid - 6, text="116" if mph else "187", fill=th["text"], anchor="w", font=self.f_b)
            c.create_text(x + 40, mid + 9, text="MPH" if mph else "KPH", fill=th["dim"], anchor="w", font=self.f_t)
        wx = x + 104
        style = self.mode_val("wheel_style")
        if show["wheel"]:
            g = "#b9bcc2"
            if style == "bar":
                c.create_rectangle(wx - 16, mid - 3, wx + 16, mid + 3, fill="#262d38", outline="")
                c.create_rectangle(wx, mid - 3, wx + 9, mid + 3, fill=g, outline="")
            elif style == "formula":
                c.create_rectangle(wx - 15, mid - 9, wx + 15, mid + 9, outline=g, width=3)
                c.create_rectangle(wx - 15, mid - 9, wx - 9, mid + 9, fill=g, outline="")
                c.create_rectangle(wx + 9, mid - 9, wx + 15, mid + 9, fill=g, outline="")
            elif style == "gt":
                c.create_arc(wx - 14, mid - 14, wx + 14, mid + 14, start=-60, extent=300, style="arc", outline=g, width=3)
                c.create_line(wx - 7, mid + 12, wx + 7, mid + 12, fill=g, width=3)
                c.create_line(wx - 14, mid, wx + 14, mid, fill=g, width=2)
            elif style == "rally":
                c.create_oval(wx - 14, mid - 14, wx + 14, mid + 14, outline=g, width=5)
                c.create_line(wx - 14, mid, wx + 14, mid, fill=g, width=2)
            else:
                c.create_oval(wx - 14, mid - 14, wx + 14, mid + 14, outline=g, width=3)
                c.create_line(wx - 14, mid, wx + 14, mid, fill=g, width=2)
        gx0, gx1 = x + 128, x + w - 40
        hh = y + h - top - 11
        if show["graph"]:
            c.create_rectangle(gx0, top, gx1, y + h - 5, fill=th["background"], outline=th["border"])
            xs = [gx0 + (gx1 - gx0) * i / 12 for i in range(13)]
            thr = [top + 3 + hh * (1 - max(0, math.sin(i * 0.7 + 1))) for i in range(13)]
            brk = [top + 3 + hh * (1 - max(0, -math.sin(i * 0.7 + 1.8))) for i in range(13)]
            c.create_line(*[v for p in zip(xs, thr) for v in p], fill=th["throttle"], width=2)
            c.create_line(*[v for p in zip(xs, brk) for v in p], fill=th["brake"], width=2)
        if show["bars"]:
            for i, (col, lvl) in enumerate([(th["clutch"], 0.0), (th["brake"], 0.2), (th["throttle"], 0.8)]):
                bx = x + w - 32 + i * 9
                c.create_rectangle(bx, top + 2, bx + 6, y + h - 5, fill="#262d38", outline="")
                c.create_rectangle(bx, y + h - 5 - (y + h - 7 - top) * lvl, bx + 6, y + h - 5, fill=col, outline="")

    def d_map(self, x, y, w, h):
        c, th = self.canvas, self.th
        mmode = self.mode_val("map_mode")
        bare = (not self.v["map_frame"].get()) and mmode != "linear"
        if bare:
            c.create_rectangle(x, y, x + w, y + h, fill=th["bg"] if "bg" in th else "#0b0e13", outline="")
        else:
            self.panel(x, y, w, h)
            self.mark(c, x + 6, y + 6, x + 9, y + 18)
            c.create_text(x + 14, y + 12, text="КАРТА", fill=th["text"], anchor="w", font=self.f_t)
        if mmode == "linear":
            self.d_map_linear(x, y, w, h)
            return
        if mmode == "local":
            self.d_map_local(x, y, w, h)
            return
        rot = self.ROT.index(self.v["map_rot"].get()) if self.v["map_rot"].get() in self.ROT else 0
        mirror = bool(self.v["map_mirror"].get())
        ccx, ccy = x + w / 2, y + h / 2 + 8

        def tf(px, py):                                     # поворот и зеркало вокруг центра карты
            dx, dy = px - ccx, py - ccy
            for _ in range(rot):
                dx, dy = -dy, dx
            return ccx + (-dx if mirror else dx), ccy + dy
        pts = []
        for i in range(60):
            a = 2 * math.pi * i / 60
            pts += list(tf(ccx + 50 * math.cos(a) + 14 * math.cos(2 * a + .7), ccy + 38 * math.sin(a) + 12 * math.sin(2 * a)))
        blk = self.mode_val("map_style") == "black"
        c.create_line(*pts, *pts[:2], fill="#ffffff" if blk else "#2f3745", width=10 if blk else 9, smooth=True)
        c.create_line(*pts, *pts[:2], fill="#1b1b1b" if blk else "#c5cad3", width=8 if blk else 3, smooth=True)
        sec_on = bool(self.v["map_sectors"].get())
        nums = bool(self.v["map_nums"].get())
        if sec_on:
            cols = ("#ffd400", "#3da9ff", "#ff5ad1")
            for j, (i0, i1) in enumerate(((0, 20), (20, 40), (40, 60))):
                seg = pts[i0 * 2:i1 * 2 + 2] if j < 2 else pts[i0 * 2:] + pts[:2]
                c.create_line(*seg, fill=cols[j], width=2, smooth=True)
            for j, idx in enumerate((20, 40)):
                px, py = pts[idx * 2], pts[idx * 2 + 1]
                c.create_line(px - 6, py - 6, px + 6, py + 6, fill=cols[j + 1], width=3)
                c.create_text(px + 12, py + 2, text=f"S{j + 2}", fill=cols[j + 1], font=self.f_t)
            px, py = pts[0], pts[1]
            c.create_rectangle(px - 2, py - 9, px + 2, py + 9, fill="#ffffff", outline="#111111")
            c.create_text(px + 22, py, text="СТАРТ", fill=th["text"], font=self.f_t)
        dot = max(3, min(12, int(self.num("map_dot", 6)))) * 0.8
        if nums:
            dot = max(dot, 6.5)
        one = bool(self.v["map_one_color"].get())
        for idx, col, pl in ((10, "#ff4d4d", 1), (24, "#2fd27e", 3), (44, "#3a8dff", 5)):
            if one or self.mode_val("map_style") == "black":
                col = "#ffffff"
            px, py = pts[idx * 2], pts[idx * 2 + 1]
            c.create_oval(px - dot, py - dot, px + dot, py + dot, fill=col, outline="")
            if nums:
                c.create_text(px, py, text=str(pl), fill="#000000" if col == "#ffffff" else "#ffffff", font=self.f_t)
        px, py = pts[60], pts[61]
        pr = dot + 2
        if self.mode_val("map_style") == "black":
            pr += 2
            c.create_oval(px - pr, py - pr, px + pr, py + pr, fill="#22c55e", outline="#ffffff", width=2)
        else:
            c.create_oval(px - pr, py - pr, px + pr, py + pr, fill="#ffffff", outline=th["accent"], width=2)
        if nums:
            c.create_text(px, py, text="2", fill="#000000", font=self.f_t)
        if self.v["map_wind"].get():
            wx, wy = x + w - 22, y + 38
            c.create_oval(wx - 13, wy - 13, wx + 13, wy + 13, fill="#12171f", outline=th["border"])
            c.create_polygon(wx + 8, wy - 9, wx + 1, wy - 1, wx + 4, wy - 1, wx - 6, wy + 9, wx - 2, wy + 1, wx - 4, wy + 1, fill="#6cb8ff", outline="")

    def d_map_linear(self, x, y, w, h):
        c, th = self.canvas, self.th
        cols = ("#ffd400", "#3da9ff", "#ff5ad1")
        x0, x1, by = x + 12, x + w - 12, y + 34
        for j, (a, b) in enumerate(((0, .31), (.31, .67), (.67, 1))):
            if self.v["map_sectors"].get():
                c.create_rectangle(x0 + (x1 - x0) * a, by, x0 + (x1 - x0) * b, by + 5, fill=cols[j], outline="")
                c.create_text(x0 + (x1 - x0) * (a + b) / 2, by - 8, text=f"S{j + 1}", fill=cols[j], font=self.f_t)
            else:
                c.create_rectangle(x0 + (x1 - x0) * a, by, x0 + (x1 - x0) * b, by + 5, fill="#c5cad3", outline="")
        for xx in (x0, x1):
            c.create_rectangle(xx - 1, by - 4, xx + 1, by + 9, fill="#ffffff", outline="")
        nums = bool(self.v["map_nums"].get())
        for ln, items in enumerate((((.40, 1, "#ff4d4d"), (.43, 2, "#ff4d4d")), ((.55, 5, "#3a8dff"), (.30, 6, "#3a8dff")),
                                    ((.50, 4, "#2fd27e"), (.80, 7, "#2fd27e")))):
            yy = by + 24 + ln * 22
            c.create_line(x0, yy, x1, yy, fill="#232a36", dash=(2, 4))
            for f, pl, col in items:
                sx = x0 + (x1 - x0) * f
                c.create_oval(sx - 7, yy - 7, sx + 7, yy + 7, fill=col, outline="")
                if nums:
                    c.create_text(sx, yy, text=str(pl), fill="#ffffff", font=self.f_t)
        px = x0 + (x1 - x0) * 0.5
        c.create_line(px, by + 8, px, by + 92, fill=th["accent"])
        c.create_oval(px - 9, by + 66 - 9 + 0, px + 9, by + 66 + 9, fill="#ffffff", outline=th["accent"], width=2)
        c.create_text(px, by + 66, text="3", fill="#000000", font=self.f_t)

    def d_map_local(self, x, y, w, h):
        c, th = self.canvas, self.th
        cx, cy = x + w / 2, y + h / 2 + 8
        r = min(w, h) / 2 - 16
        c.create_oval(cx - r, cy - r, cx + r, cy + r, fill="#0e1218", outline=th["border"], width=2)
        pts = [cx - 4, cy + r - 4, cx - 2, cy + r * .5, cx, cy, cx + r * .2, cy - r * .35, cx + r * .55, cy - r * .55, cx + r * .8, cy - r * .5]
        blk = self.mode_val("map_style") == "black"
        c.create_line(*pts, fill="#ffffff" if blk else "#2f3745", width=10 if blk else 9, smooth=True)
        c.create_line(*pts, fill="#1b1b1b" if blk else "#c5cad3", width=8 if blk else 3, smooth=True)
        if self.v["map_sectors"].get():
            c.create_line(cx + r * .2 - 6, cy - r * .35 - 6, cx + r * .2 + 6, cy - r * .35 + 6, fill="#3da9ff", width=3)
        nums = bool(self.v["map_nums"].get())
        for px, py, col, pl in ((cx + r * .55, cy - r * .55, "#ff4d4d", 2), (cx - 3, cy + r * .62, "#2fd27e", 4)):
            c.create_oval(px - 7, py - 7, px + 7, py + 7, fill=col, outline="")
            if nums:
                c.create_text(px, py, text=str(pl), fill="#ffffff", font=self.f_t)
        c.create_oval(cx - 9, cy - 9, cx + 9, cy + 9, fill="#ffffff", outline=th["accent"], width=2)
        if nums:
            c.create_text(cx, cy, text="3", fill="#000000", font=self.f_t)
        if self.v["map_headup"].get():
            c.create_polygon(cx, cy - 22, cx - 5, cy - 13, cx + 5, cy - 13, fill=th["accent"], outline="")
        c.create_text(x + w - 8, y + 12, text=f"{self.v['map_radius'].get()} м", fill=th["dim"], anchor="e", font=self.f_t)

    def delta_th_h(self):
        n = any(self.v[k].get() for k in ("delta_show_session", "delta_show_mine", "delta_show_pred"))
        t = self.mode_val("delta_theme")
        return 100 if t == "ring" else (74 if n else 34)

    def d_delta_theme(self, x, y, w, h, t):
        c = self.canvas
        G, R, D = "#24e84e", "#ff4a40", "#060708"
        names = [(k, n) for k, n in (("delta_show_session", "ЛУЧШИЙ СЕССИИ"), ("delta_show_mine", "МОЙ ЛУЧШИЙ"), ("delta_show_pred", "ПРОГНОЗ")) if self.v[k].get()]
        def cards(x0, y0, ww, hh):
            if not names:
                return
            gap = 4
            cw = (ww - gap * (len(names) - 1)) / len(names)
            for i, (k, n) in enumerate(names):
                cx = x0 + i * (cw + gap)
                c.create_rectangle(cx, y0, cx + cw, y0 + hh, fill=D, outline="")
                c.create_text(cx + cw / 2, y0 + hh * 0.38, text="01:38.12", fill=G if k.endswith("pred") else "#fff", font=self.f_t)
                c.create_text(cx + cw / 2, y0 + hh * 0.75, text=n, fill="#aab", font=("Segoe UI", 5, "bold"))
        if t == "ring":
            d = min(h - 4, 92)
            x0, y0 = x + 4, y + 2
            c.create_oval(x0, y0, x0 + d, y0 + d, fill=D, outline="")
            c.create_arc(x0 + 6, y0 + 6, x0 + d - 6, y0 + d - 6, start=0, extent=359.9, style="arc", outline="#2a2d31", width=7)
            c.create_arc(x0 + 6, y0 + 6, x0 + d - 6, y0 + d - 6, start=90, extent=95, style="arc", outline=G, width=7)
            c.create_rectangle(x0 + d * .15, y0 + d * .38, x0 + d * .85, y0 + d * .66, fill="#000", outline="")
            c.create_text(x0 + d / 2, y0 + d / 2 + 2, text="-0.85", fill=G, font=self.f_b)
            if names:
                ch = 50
                cards(x0 + d + 6, y0 + (d - ch) / 2, w - d - 14, ch)
            return
        top = 16 if t in ("tick", "pill") else 0
        bh = {"wide": 24, "pill": 8, "tick": 6, "segments": 24, "chevrons": 24}[t]
        bx0, bx1 = x + 6, x + w - 6
        y0 = y + 4 + top
        cy = y0 + bh / 2
        mid = (bx0 + bx1) / 2
        if t in ("wide", "segments", "chevrons"):
            c.create_rectangle(bx0, y0, bx1, y0 + bh, fill=D, outline="")
        elif t == "tick":
            c.create_rectangle(bx0, y0, bx1, y0 + bh, fill=D, outline="")
        else:
            c.create_line(bx0, cy, bx1, cy, fill="#101214", width=8, capstyle="round")
        if t == "wide":
            c.create_rectangle(mid, y0 + 5, mid + 50, y0 + bh - 5, fill=G, outline="")
            c.create_text(bx1 - 4, cy, text="-0.85", fill=G, anchor="e", font=self.f_s)
        elif t == "segments":
            for i in range(30):
                xx = bx0 + 6 + (bx1 - bx0 - 50) * i / 29
                c.create_line(xx, y0 + 5, xx, y0 + bh - 5, fill=G if 10 <= i <= 17 else "#3a3d42", width=2)
            c.create_text(bx1 - 4, cy, text="-0.85", fill=G, anchor="e", font=self.f_s)
        elif t == "chevrons":
            for i in range(4):
                xx = mid + 8 + i * 12
                c.create_line(xx, cy - 6, xx + 6, cy, xx, cy + 6, fill=G, width=2)
            c.create_text(mid - 10, cy, text="-0.85", fill=G, anchor="e", font=self.f_s)
        elif t == "tick":
            c.create_line(mid, cy, mid + 50, cy, fill=G, width=3)
            c.create_rectangle(mid - 22, y + 2, mid + 22, y + 15, fill=D, outline="")
            c.create_text(mid, y + 8, text="-0.85", fill=G, font=self.f_s)
        else:
            c.create_rectangle(mid + 24, y + 2, mid + 66, y + 16, fill=G, outline="")
            c.create_text(mid + 45, y + 9, text="-0.85", fill="#04210b", font=self.f_s)
            c.create_oval(mid + 45 - 3, cy - 3, mid + 45 + 3, cy + 3, fill=G, outline="")
        cards(bx0, y0 + bh + 6, bx1 - bx0, 36)

    def d_delta(self, x, y, w, h):
        c, th = self.canvas, self.th
        if self.mode_val("delta_theme") not in ("full", "bar", "sectors"):
            return self.d_delta_theme(x, y, w, h, self.mode_val("delta_theme"))
        self.panel(x, y, w, h)
        if self.mode_val("delta_theme") == "sectors":
            self.mark(c, x + 6, y + 6, x + 9, y + 18)
            c.create_text(x + 14, y + 12, text="ДЕЛЬТА", fill=th["text"], anchor="w", font=self.f_t)
            c.create_text(x + w - 8, y + 12, text="к лучшему", fill=th["dim"], anchor="e", font=self.f_t)
            c.create_text(x + 10, y + 30, text="КРУГ", fill=th["dim"], anchor="w", font=self.f_t)
            c.create_text(x + 10, y + 46, text="1:41.37", fill=th["text"], anchor="w", font=self.f_b)
            c.create_text(x + w - 8, y + 30, text="ПРОГНОЗ", fill=th["dim"], anchor="e", font=self.f_t)
            c.create_text(x + w - 8, y + 46, text="1:40.55", fill=th["text"], anchor="e", font=self.f_s)
            cell = (w - 20) / 3
            for i, (v, dd) in enumerate((("28.900", "-0.200"), ("31.800", "-0.100"), ("29.100", "+0.100"))):
                mx = x + 10 + i * cell + cell / 2
                c.create_text(mx, y + 70, text="S%d" % (i + 1), fill=th["dim"], font=self.f_t)
                c.create_text(mx, y + 84, text=v, fill=th["text"], font=self.f_t)
                c.create_text(mx, y + 99, text=dd, fill="#2ee26d" if dd[0] == "-" else "#ff5a4f", font=self.f_s)
            if self.v["delta_sec_bar"].get():
                c.create_text(x + w / 2, y + 122, text="-0.34", fill="#2ee26d", font=self.f_s)
                bx0, bx1, by = x + 10, x + w - 10, y + 134
                c.create_rectangle(bx0, by, bx1, by + 7, fill="#262d38", outline="")
                mid = (bx0 + bx1) / 2
                c.create_rectangle(mid - (mid - bx0) * 0.34, by, mid, by + 7, fill="#2ee26d", outline="")
                c.create_line(mid, by - 2, mid, by + 9, fill=th["text"], width=2)
            return
        if self.mode_val("delta_theme") == "bar":
            c.create_text(x + 10, y + h / 2, text="-0.34", fill="#2ee26d", anchor="w", font=self.f_s)
            bx0, bx1, by = x + 52, x + w - 10, y + h / 2
            c.create_rectangle(bx0, by - 4, bx1, by + 4, fill="#262d38", outline="")
            mid = (bx0 + bx1) / 2
            c.create_rectangle(mid - (mid - bx0) * 0.34, by - 4, mid, by + 4, fill="#2ee26d", outline="")
            c.create_line(mid, by - 7, mid, by + 7, fill=th["text"], width=2)
            return
        self.mark(c, x + 6, y + 6, x + 9, y + 18)
        c.create_text(x + 14, y + 12, text="ДЕЛЬТА", fill=th["text"], anchor="w", font=self.f_t)
        c.create_text(x + w - 8, y + 12, text="к лучшему" if self.mode_val("delta_ref") == "best" else "к прошлому", fill=th["dim"], anchor="e", font=self.f_t)
        c.create_text(x + w / 2, y + 40, text="-0.34", fill="#2ee26d", font=self.f_b)
        bx0, bx1, by = x + 10, x + w - 10, y + 60
        c.create_rectangle(bx0, by, bx1, by + 7, fill="#262d38", outline="")
        mid = (bx0 + bx1) / 2
        c.create_rectangle(mid - (mid - bx0) * 0.34, by, mid, by + 7, fill="#2ee26d", outline="")
        c.create_line(mid, by - 2, mid, by + 9, fill=th["text"], width=2)
        pts = []
        for i in range(30):
            pts += [bx0 + (bx1 - bx0) * i / 29, y + 86 - math.sin(i / 4.0) * 8 + i * 0.2]
        c.create_line(*pts, fill="#2ee26d", width=2)
        c.create_text(x + 10, y + h - 8, text="эталон 1:41.230", fill=th["dim"], anchor="w", font=self.f_t)

    def d_alerts(self, x, y, w, h):
        c, th = self.canvas, self.th
        for i, (bg, fg, tx) in enumerate((("#c62828", "#ffffff", "ШТРАФ  (1)"), ("#f2b705", "#111111", "ЖЁЛТЫЙ ФЛАГ"), ("#1e5aa8", "#ffffff", "ПОСЛЕДНИЙ КРУГ"))):
            hh = (h - 8) / 3
            yy = y + i * (hh + 4)
            self.panel(x, yy, w, hh, fill=bg, r=6)
            c.create_text(x + w / 2, yy + hh / 2, text=tx, fill=fg, font=self.f_s)

    def d_tyres(self, x, y, w, h):
        c, th = self.canvas, self.th
        self.panel(x, y, w, h)
        if self.mode_val("tyres_view") == "wear":
            self.mark(c, x + 6, y + 6, x + 9, y + 18)
            c.create_text(x + 14, y + 12, text="ИЗНОС ШИН", fill=th["text"], anchor="w", font=self.f_t)
            c.create_text(x + w - 8, y + 12, text="M/M", fill=th["dim"], anchor="e", font=self.f_t)
            cell = (w - 24) / 2
            for i, (rem, lap, lc) in enumerate(((94.4, "1.6", "#ff5a4f"), (93.2, "0.8", "#2ee26d"), (92.2, "1.2", "#ffc107"), (91.2, "1.1", "#ffc107"))):
                x0 = x + 8 + (i % 2) * (cell + 8)
                cy = y + 36 + (i // 2) * 28
                c.create_text(x0, cy, text=f"{rem:.1f}", fill="#2ee26d", anchor="w", font=self.f_s)
                bx0, bx1 = x0 + 34, x0 + cell - 38
                c.create_rectangle(bx0, cy - 5, bx1, cy + 5, fill="#262d38", outline="")
                c.create_rectangle(bx0, cy - 5, bx0 + (bx1 - bx0) * rem / 100, cy + 5, fill="#2ee26d", outline="")
                c.create_rectangle(x0 + cell - 34, cy - 4, x0 + cell - 26, cy + 4, fill=lc, outline="")
                c.create_text(x0 + cell - 22, cy, text=lap, fill=th["text"], anchor="w", font=self.f_t)
            c.create_text(x + w - 8, y + h - 8, text="остаток % и износ за круг", fill=th["dim"], anchor="e", font=self.f_t)
            return
        if self.mode_val("tyres_view") == "compact":
            self.mark(c, x + 6, y + 6, x + 9, y + 18)
            c.create_text(x + 14, y + 12, text="ШИНЫ", fill=th["text"], anchor="w", font=self.f_t)
            c.create_text(x + w - 8, y + 12, text="M/M", fill=th["dim"], anchor="e", font=self.f_t)
            tw, thh = (w - 24) * 0.40, (h - 40) / 2
            c.create_rectangle(x + w / 2 - tw * 0.3, y + 34, x + w / 2 + tw * 0.3, y + 34 + 2 * thh, fill="#161d27", outline=th["border"] if "border" in th else "#2a3340")
            for i, (t, wear, pr, br) in enumerate(((92, .93, "1.68", 498), (96, .91, "1.71", 509), (88, .92, "1.73", 668), (101, .9, "1.75", 829))):
                x0 = x + 8 if i % 2 == 0 else x + w - 8 - tw
                y0 = y + 30 + (i // 2) * (thh + 6)
                col = "#2ee26d" if t < 105 else "#ffc107"
                c.create_rectangle(x0, y0, x0 + tw, y0 + thh, fill="#0e131a", outline=col, width=2)
                c.create_rectangle(x0 + 4, y0 + 4, x0 + 7, y0 + thh - 4, fill="#262d38", outline="")
                c.create_rectangle(x0 + 4, y0 + thh - 4 - (thh - 8) * wear, x0 + 7, y0 + thh - 4, fill="#2ee26d", outline="")
                cx = x0 + tw / 2 + 3
                c.create_text(cx, y0 + thh * 0.22, text=f"{t}°", fill=col, font=self.f_s)
                c.create_text(cx, y0 + thh * 0.48, text=pr, fill=th["text"], font=self.f_t)
                c.create_text(cx, y0 + thh * 0.68, text=f"{wear * 100:.0f}%", fill="#2ee26d", font=self.f_t)
                c.create_text(cx, y0 + thh * 0.86, text=f"т{br}", fill=th["dim"], font=self.f_t)
            return
        self.mark(c, x + 6, y + 6, x + 9, y + 18)
        c.create_text(x + 14, y + 12, text="ШИНЫ И ТОРМОЗА", fill=th["text"], anchor="w", font=self.f_t)
        c.create_text(x + w - 8, y + 12, text="M/M", fill=th["dim"], anchor="e", font=self.f_t)
        for i, (t, wear, br) in enumerate(((92, .93, 498), (96, .91, 509), (88, .92, 668), (101, .9, 829))):
            cx0 = x + 8 + (i % 2) * (w / 2 - 4)
            cy0 = y + 34 + (i // 2) * 60
            col = "#2ee26d" if t < 105 else "#ffc107"
            c.create_text(cx0, cy0, text=f"{t}°", fill=col, anchor="w", font=self.f_b)
            c.create_text(cx0 + w / 2 - 14, cy0, text="1.68 бар", fill=th["text"], anchor="e", font=self.f_t)
            c.create_rectangle(cx0, cy0 + 14, cx0 + w / 2 - 14, cy0 + 19, fill="#262d38", outline="")
            c.create_rectangle(cx0, cy0 + 14, cx0 + (w / 2 - 14) * wear, cy0 + 19, fill="#2ee26d", outline="")
            c.create_text(cx0, cy0 + 28, text=f"тормоз {br}°", fill=th["dim"], anchor="w", font=self.f_t)

    def d_pitplan(self, x, y, w, h):
        c, th = self.canvas, self.th
        self.panel(x, y, w, h)
        self.mark(c, x + 6, y + 6, x + 9, y + 18)
        c.create_text(x + 14, y + 12, text="ПИТ-СТОП И СТИНТ", fill=th["text"], anchor="w", font=self.f_t)
        rows = [("Стинт", "42:10 • 18 кр."), ("Топлива хватит на", "6.2 кр."), ("Энергии хватит на", "5.4 кр."),
                ("Заезд в боксы", "через 4 кр."), ("Последний стоп", "48.2 с"), ("За рулём ZINOVIEV", "1:42:10")]
        for i, (a, b) in enumerate(rows):
            yy = y + 32 + i * 18
            c.create_text(x + 10, yy, text=a, fill=th["dim"], anchor="w", font=self.f_t)
            c.create_text(x + w - 10, yy, text=b, fill=th["accent"] if i == 3 else th["text"], anchor="e", font=self.f_t)

    def d_engine(self, x, y, w, h):
        c, th = self.canvas, self.th
        self.panel(x, y, w, h)
        view = self.mode_val("engine_view")
        wc = lambda p: "#2ee26d" if p > 0.5 else "#ffc107" if p > 0.25 else "#ff3b30"
        wear = (0.95, 0.94, 0.93, 0.92)
        if view == "compact":
            mid = y + h / 2
            c.create_text(x + 10, mid, text="В", fill=th["dim"], anchor="w", font=self.f_t)
            c.create_text(x + 22, mid, text="93°", fill="#2ee26d", anchor="w", font=self.f_s)
            c.create_text(x + 62, mid, text="М", fill=th["dim"], anchor="w", font=self.f_t)
            c.create_text(x + 74, mid, text="111°", fill="#2ee26d", anchor="w", font=self.f_s)
            c.create_text(x + 122, mid, text="31.4 л  3.69/кр  ~8 кр", fill=th["text"], anchor="w", font=self.f_t)
            c.create_text(x + 262, mid, text="ШИНЫ", fill=th["dim"], anchor="w", font=self.f_t)
            for i, v in enumerate(wear):
                c.create_text(x + 300 + i * 14, mid, text=f"{v * 100:.0f}", fill=wc(v), anchor="w", font=self.f_t)
            return
        self.mark(c, x + 6, y + 6, x + 9, y + 18)
        c.create_text(x + 14, y + 12, text="ДВИГАТЕЛЬ" if view == "gauges" else "ДВИГАТЕЛЬ И РАСХОД", fill=th["text"], anchor="w", font=self.f_t)
        if view == "gauges":
            for cx, val, name, fr in ((x + w * 0.27, "93°", "ВОДА", 0.55), (x + w * 0.73, "111°", "МАСЛО", 0.65)):
                r = 30
                c.create_arc(cx - r, y + 62 - r, cx + r, y + 62 + r, start=-45, extent=270, style="arc", outline="#262d38", width=6)
                c.create_arc(cx - r, y + 62 - r, cx + r, y + 62 + r, start=225, extent=-270 * fr, style="arc", outline="#2ee26d", width=6)
                c.create_text(cx, y + 60, text=val, fill="#2ee26d", font=self.f_s)
                c.create_text(cx, y + 80, text=name, fill=th["dim"], font=self.f_t)
            c.create_text(x + 10, y + 114, text="31.4 л", fill=th["text"], anchor="w", font=self.f_s)
            c.create_text(x + w - 10, y + 114, text="3.69 л/кр  ~8 кр", fill=th["dim"], anchor="e", font=self.f_t)
            top = y + 134
        else:
            for i, (nm, val, fr) in enumerate((("ВОДА", "93°", 0.55), ("МАСЛО", "111°", 0.65))):
                yy = y + 34 + i * 22
                c.create_text(x + 10, yy, text=nm, fill=th["dim"], anchor="w", font=self.f_t)
                c.create_rectangle(x + 56, yy - 4, x + w - 46, yy + 4, fill="#262d38", outline="")
                c.create_rectangle(x + 56, yy - 4, x + 56 + (w - 102) * fr, yy + 4, fill="#2ee26d", outline="")
                c.create_text(x + w - 10, yy, text=val, fill="#2ee26d", anchor="e", font=self.f_s)
            c.create_text(x + 10, y + 86, text="ТОПЛИВО", fill=th["dim"], anchor="w", font=self.f_t)
            c.create_text(x + w - 10, y + 86, text="31.4 л", fill=th["text"], anchor="e", font=self.f_s)
            c.create_text(x + 10, y + 102, text="расход 3.69 л/кр  •  хватит на 8.5 кр.", fill=th["text"], anchor="w", font=self.f_t)
            c.create_text(x + 10, y + 128, text="ОСТАТОК РЕЗИНЫ", fill=th["dim"], anchor="w", font=self.f_t)
            top = y + 146
        cell = (w - 28) / 2
        for i, v in enumerate(wear):
            bx = x + 10 + (i % 2) * (cell + 8)
            by = top + 8 + (i // 2) * 20
            c.create_text(bx, by, text=f"{v * 100:.1f}", fill=wc(v), anchor="w", font=self.f_t)
            c.create_rectangle(bx + 30, by - 3, bx + cell, by + 3, fill="#262d38", outline="")
            c.create_rectangle(bx + 30, by - 3, bx + 30 + (cell - 30) * v, by + 3, fill=wc(v), outline="")

    def d_gaps(self, x, y, w, h):
        c, th = self.canvas, self.th
        self.panel(x, y, w, h)
        self.mark(c, x + 6, y + 6, x + 9, y + 18)
        c.create_text(x + 14, y + 12, text="СОПЕРНИКИ", fill=th["text"], anchor="w", font=self.f_t)
        c.create_text(x + w - 8, y + 12, text="P4/9", fill=th["dim"], anchor="e", font=self.f_t)
        for i, (t, name, g, tr_, col) in enumerate((("ВПЕРЕДИ", "#12 LYNN", "+2.4", "▼ 0.35 с/кр.  догоните через ~7 кр.", "#2ee26d"),
                                                     ("ПОЗАДИ", "#92 LIETZ", "+1.1", "▼ 0.20 с/кр.  догонит через ~5 кр.", "#ff5a4f"))):
            yy = y + 34 + i * 46
            c.create_text(x + 10, yy, text=t, fill=th["dim"], anchor="w", font=self.f_t)
            c.create_text(x + 10, yy + 14, text=name, fill=th["text"], anchor="w", font=self.f_s)
            c.create_text(x + w - 10, yy + 12, text=g, fill=th["text"], anchor="e", font=self.f_b)
            c.create_text(x + 10, yy + 30, text=tr_, fill=col, anchor="w", font=self.f_t)

    def speedo_size(self):
        return {"arc": (170, 170), "dial": (170, 170), "tacho": (170, 170), "led": (260, 70),
                "digital": (170, 100), "minimal": (110, 48)}.get(self.mode_val("speedo_view"), (170, 170))

    def d_speedo(self, x, y, w, h):
        c, th = self.canvas, self.th
        v = self.mode_val("speedo_view")
        acc = th.get("accent", "#f2b705")
        G, R_, D = "#2ee26d", "#ff3b30", "#262d38"
        if v != "minimal":
            self.panel(x, y, w, h)
        cx, cy = x + w / 2, y + h / 2
        def pt(r, a):
            return cx + r * math.cos(math.radians(a)), cy - r * math.sin(math.radians(a))
        if v == "arc":
            r = w * 0.43
            c.create_arc(cx - r, cy - r, cx + r, cy + r, start=225, extent=-270, style="arc", outline=D, width=9)
            c.create_arc(cx - r, cy - r, cx + r, cy + r, start=225, extent=-190, style="arc", outline=G, width=9)
            c.create_arc(cx - r, cy - r, cx + r, cy + r, start=35, extent=-0, style="arc", outline=R_, width=9)
            r2 = r - 13
            c.create_arc(cx - r2, cy - r2, cx + r2, cy + r2, start=245, extent=-80, style="arc", outline=G, width=4)
            c.create_arc(cx - r2, cy - r2, cx + r2, cy + r2, start=295, extent=30, style="arc", outline=R_, width=4)
            c.create_text(cx, cy - h * 0.2, text="5", fill=th["text"], font=self.f_b)
            c.create_text(cx, cy + h * 0.05, text="255", fill=th["text"], font=self.f_s)
            c.create_text(cx, cy + h * 0.18, text="KPH", fill=th["dim"], font=self.f_t)
        elif v in ("dial", "tacho"):
            r = w * 0.46
            c.create_oval(cx - r, cy - r, cx + r, cy + r, fill="#0d0e10", outline="#c9ccd2", width=3)
            n = 12 if v == "dial" else 9
            for i in range(n + 1):
                a = 225 - 270 * i / n
                x0, y0 = pt(r * 0.8, a)
                x1, y1 = pt(r * 0.92, a)
                c.create_line(x0, y0, x1, y1, fill=acc, width=2)
                tx, ty = pt(r * 0.64, a)
                c.create_text(tx, ty, text=str(i * (30 if v == "dial" else 1) if v == "dial" else i), fill=acc, font=self.f_t)
            xa, ya = pt(r * 0.8, 225 - 270 * (0.74 if v == "dial" else 0.8))
            c.create_line(cx, cy, xa, ya, fill="#ffffff", width=3)
            if v == "dial":
                c.create_oval(cx - 6, cy - 6, cx + 6, cy + 6, fill="#1b1d20", outline="#555")
                c.create_text(cx, cy + r * 0.55, text="255", fill=th["text"], font=self.f_s)
            else:
                c.create_text(cx, cy - r * 0.05, text="5", fill=acc, font=self.f_b)
                c.create_text(cx, cy + r * 0.4, text="255", fill=th["text"], font=self.f_s)
        elif v == "led":
            n = 22
            lw = (w - 20 - 3 * (n - 1)) / n
            for i in range(n):
                col = G if i < 10 else R_ if i < 17 else "#4a90ff"
                lx = x + 10 + i * (lw + 3)
                c.create_rectangle(lx, y + 8, lx + lw, y + 22, fill=col if i < 15 else D, outline="")
            c.create_text(x + w * 0.09, y + h * 0.66, text="5", fill=acc, font=self.f_b)
            c.create_text(x + w * 0.5, y + h * 0.64, text="255", fill=th["text"], font=self.f_b)
            c.create_text(x + w * 0.5 + 32, y + h * 0.8, text="KPH", fill=th["dim"], anchor="w", font=self.f_t)
            c.create_rectangle(x + w - 28, y + h * 0.45, x + w - 18, y + h - 8, fill=G, outline="")
            c.create_rectangle(x + w - 14, y + h * 0.8, x + w - 6, y + h - 8, fill=R_, outline="")
        elif v == "digital":
            self.panel(x, y, w, h)
            self.canvas.create_rectangle(x + 8, y + 8, x + w * 0.28, y + h * 0.6, fill=acc, outline="")
            c.create_text(x + w * 0.14 + 4, y + h * 0.34, text="5", fill="#111", font=self.f_b)
            c.create_text(x + w - 10, y + h * 0.3, text="255", fill=th["text"], anchor="e", font=self.f_b)
            c.create_text(x + w - 10, y + h * 0.52, text="KPH", fill=th["dim"], anchor="e", font=self.f_t)
            c.create_rectangle(x + 8, y + h * 0.72, x + w - 8, y + h * 0.8, fill=D, outline="")
            c.create_rectangle(x + 8, y + h * 0.72, x + w * 0.7, y + h * 0.8, fill=acc, outline="")
            c.create_rectangle(x + 8, y + h * 0.86, x + w * 0.8, y + h * 0.9, fill=G, outline="")
            c.create_rectangle(x + 8, y + h * 0.92, x + w * 0.3, y + h * 0.96, fill=R_, outline="")
        else:
            c.create_rectangle(x, y, x + w, y + h, fill=th["panel"], outline="")
            c.create_text(x + w * 0.16, cy, text="5", fill=acc, font=self.f_b)
            c.create_text(x + w * 0.72, cy - 4, text="255", fill=th["text"], anchor="e", font=self.f_b)
            c.create_text(x + w * 0.76, cy + 8, text="KPH", fill=th["dim"], anchor="w", font=self.f_t)

    def d_sesstime(self, x, y, w, h):
        c, th = self.canvas, self.th
        self.panel(x, y, w, h)
        self.mark(c, x + 6, y + 6, x + 9, y + 18)
        c.create_text(x + 14, y + 12, text="RACE 1", fill=th["text"], anchor="w", font=self.f_t)
        c.create_text(x + w - 8, y + 12, text="ОСТАЛОСЬ", fill=th["dim"], anchor="e", font=self.f_t)
        c.create_text(x + w / 2, y + h * 0.68, text="1:23:45", fill=th["text"], font=self.f_b)

    def d_clock(self, x, y, w, h):
        c, th = self.canvas, self.th
        self.panel(x, y, w, h)
        self.mark(c, x + 6, y + 6, x + 9, y + 18)
        c.create_text(x + 14, y + 12, text="ВРЕМЯ", fill=th["text"], anchor="w", font=self.f_t)
        c.create_text(x + w - 8, y + 12, text=time.strftime("%d.%m"), fill=th["dim"], anchor="e", font=self.f_t)
        c.create_text(x + w / 2, y + h * 0.68, text=time.strftime("%H:%M:%S" if self.v["clock_seconds"].get() else "%H:%M"),
                      fill=th["text"], font=self.f_b)

    def d_fuel(self, x, y, w, h):
        c, th = self.canvas, self.th
        self.panel(x, y, w, h)
        self.mark(c, x + 6, y + 6, x + 9, y + 18)
        c.create_text(x + 14, y + 12, text="ТОПЛИВО", fill=th["text"], anchor="w", font=self.f_t)
        try:
            reserve = float(self.v["fuel_reserve"].get())
        except (tk.TclError, ValueError):
            reserve = 0.0
        if reserve:
            c.create_text(x + w - 8, y + 12, text=f"+{reserve:g} кр.", fill=th["dim"], anchor="e", font=self.f_t)
        avg = int(self.num("fuel_avg", 5, int))
        c.create_text(x + 8, y + 36, text="47.3 л", fill=th["text"], anchor="w", font=self.f_b)
        c.create_rectangle(x + 8, y + 52, x + w - 8, y + 58, fill="#262d38", outline="")
        c.create_rectangle(x + 8, y + 52, x + 8 + (w - 16) * 0.55, y + 58, fill="#2ee26d", outline="")
        add = 12 + reserve * 2.8
        for i, (a, b) in enumerate([("2.84 л", "последний круг"), ("2.81 л", f"среднее ({avg} кр.)"), (f"+{add:.0f} л", "добавить")]):
            yy = y + 80 + i * 28
            c.create_text(x + 8, yy, text=a, fill=th["text"] if i < 2 else "#ff5a4f", anchor="w", font=self.f_s)
            c.create_text(x + 8, yy + 12, text=b, fill=th["dim"], anchor="w", font=self.f_t)
        if self.game == "lmu" and self.v["fuel_ve"].get() and h >= 200:
            yv = y + 160
            c.create_line(x + 8, yv - 6, x + w - 8, yv - 6, fill=th["border"])
            c.create_text(x + 8, yv + 4, text="ВИРТ. ЭНЕРГИЯ", fill=th["dim"], anchor="w", font=self.f_t)
            c.create_text(x + 8, yv + 20, text="61.4%", fill=th["text"], anchor="w", font=self.f_s)
            c.create_text(x + w - 8, yv + 20, text="9.8 кр.", fill=th["accent"], anchor="e", font=self.f_s)
            c.create_rectangle(x + 8, yv + 32, x + w - 8, yv + 37, fill="#262d38", outline="")
            c.create_rectangle(x + 8, yv + 32, x + 8 + (w - 16) * 0.61, yv + 37, fill="#2ee26d", outline="")
            c.create_text(x + 8, yv + 50, text="за круг 6.2%", fill=th["dim"], anchor="w", font=self.f_t)

    def d_lights(self, x, y, w, h):
        c = self.canvas
        n = 5
        step = w / n
        for i in range(n):
            cx, cy, r = x + step * (i + 0.5), y + h / 2, h * 0.38
            c.create_oval(cx - r - 2, cy - r - 2, cx + r + 2, cy + r + 2, fill="#1a1f29", outline="#05070a")
            c.create_oval(cx - r, cy - r, cx + r, cy + r, fill="#ff2a22" if i < 3 else "#2a2f39", outline="")

    def d_apps(self, x, y, w, h):
        c, th = self.canvas, self.th
        self.panel(x, y, w, h)
        c.create_text(x + 12, y + 14, text=f"{GAMES[self.game]} + оверлей", fill=th["accent"], anchor="w", font=self.f_b)
        items = companions_for(self.game) + [(c["id"], c["name"]) for c in self.app.cfg["custom_apps"]]
        rh = (h - 34) / len(items)
        for i, (key, name) in enumerate(items):
            yy = y + 30 + i * rh
            on = (bool(self.v["app_" + key].get()) and companion_runs(key, self.game)) if key.startswith("c_") is False else self.game in next(
                (c["games"] for c in self.app.cfg["custom_apps"] if c["id"] == key), [])
            found = bool(resolve_app(key, self.app.cfg))
            c.create_rectangle(x + 8, yy, x + w - 8, yy + rh - 4, fill=mix(th["panel"], "#ffffff", 0.05), outline="")
            c.create_oval(x + 16, yy + rh / 2 - 6, x + 28, yy + rh / 2 + 4, fill="#2ee26d" if on else "#4a5260", outline="")
            c.create_text(x + 36, yy + rh / 2 - 2, text=name, fill=th["text"] if on else th["dim"], anchor="w", font=self.f_s)
            c.create_text(x + w - 14, yy + rh / 2 - 2, text=("запустится" if on else ("только для iRacing" if key.startswith("c_") is False and not companion_runs(key, self.game) else "не запускается")) if (found or not on) else "не найдена",
                          fill=th["dim"] if (found or not on) else "#ff5a4f", anchor="e", font=self.f_t)

    def d_limiter(self, x, y, w, h):
        c, th = self.canvas, self.th
        self.panel(x, y, w, h, fill="#0d1015", r=14)
        c.create_oval(x + 10, y + h / 2 - 9, x + 28, y + h / 2 + 9, fill="#2ee26d", outline="#17803f", width=2)
        c.create_text(x + 38, y + 12, text="PIT LIMITER", fill=th["dim"], anchor="w", font=self.f_t)
        c.create_text(x + 38, y + h - 12, text="ON", fill="#2ee26d", anchor="w", font=self.f_s)


# ---------------------------------------------------------------------------
# статистика сессий
# ---------------------------------------------------------------------------
STATS_DIR = os.path.join(HERE, "stats")


def fmt_time(t, short=False):
    """72.345 -> 1:12.345 (пусто, если данных нет)."""
    if not isinstance(t, (int, float)) or t <= 0:
        return "—"
    m, s = divmod(t, 60)
    return f"{int(m)}:{s:06.3f}" if m or not short else f"{s:.3f}"


def fmt_num(v, nd=1, suffix=""):
    return "—" if not isinstance(v, (int, float)) else f"{v:.{nd}f}{suffix}"


def load_sessions():
    """Список сохранённых сессий: [(путь, данные)] от новых к старым."""
    out = []
    try:
        names = [n for n in os.listdir(STATS_DIR) if n.lower().endswith(".json")]
    except OSError:
        return out
    for n in names:
        path = os.path.join(STATS_DIR, n)
        d = read_json(path)
        if isinstance(d.get("laps"), list):
            out.append((path, d, os.path.getmtime(path)))
    out.sort(key=lambda x: -x[2])
    return [(p, d) for p, d, _ in out]


def session_title(d):
    game = {"lmu": "LMU", "rf2": "rF2", "iracing": "iRacing"}.get(d.get("game"), str(d.get("game", "")).upper())
    return f"{game} · {d.get('track') or '?'} · {d.get('label') or d.get('kind', '')}"


def summarize(d):
    """Итоги по сессии для карточек сверху."""
    laps = [x for x in d.get("laps", []) if isinstance(x, dict)]
    timed = [x for x in laps if x.get("time")]
    clean = [x for x in timed if not x.get("pit")]
    avg = lambda vals: (sum(vals) / len(vals)) if vals else None
    fuel = [x["fuel_used"] for x in laps if isinstance(x.get("fuel_used"), (int, float)) and x["fuel_used"] > 0]
    wear = [sum(x["tyre_used"]) / len(x["tyre_used"]) * 100 for x in laps if x.get("tyre_used")]
    vmax = [x["vmax"] for x in laps if x.get("vmax")]
    vmin = [x["vmin"] for x in laps if x.get("vmin")]
    return {"laps": len(laps), "best": min((x["time"] for x in timed), default=None), "avg": avg([x["time"] for x in clean]),
            "fuel": avg(fuel), "wear": avg(wear), "vmax": max(vmax, default=None), "vmin": min(vmin, default=None),
            "best_s": [min((x[k] for x in laps if x.get(k)), default=None) for k in ("s1", "s2", "s3")]}


def driver_view(d, key):
    """Сессия «глазами» другого пилота: те же поля кругов, что у игрока, там где они есть в записи."""
    ent = (d.get("drivers") or {}).get(str(key))
    if not ent:
        return None
    rows = sorted((r for r in ent.get("laps", []) if isinstance(r, list) and len(r) >= 6), key=lambda r: r[0])
    laps, prev = [], None
    for r in rows:
        no, t, s1, s2, s3, pit = r[:6]
        fuel = r[6] if len(r) > 6 else None
        w = r[7] if len(r) > 7 and isinstance(r[7], list) and len(r[7]) == 4 else None
        used = fuel_used = None
        if prev and not pit and not prev["pit"]:
            if isinstance(fuel, (int, float)) and isinstance(prev["fuel"], (int, float)) and 0 < prev["fuel"] - fuel < 20:
                fuel_used = prev["fuel"] - fuel
            if w and prev["w"]:
                dd = [a - b for a, b in zip(prev["w"], w)]
                if all(-0.02 < v < 0.15 for v in dd):
                    used = [max(0.0, v) for v in dd]
        laps.append({"lap": no, "time": t, "s1": s1, "s2": s2, "s3": s3, "fuel_used": fuel_used, "fuel_left": fuel,
                     "tyres": w, "tyre_used": used, "vmax": None, "vmin": None, "pit": bool(pit), "at": ""})
        prev = {"pit": bool(pit), "fuel": fuel, "w": w}
    out = dict(d)
    out.update(laps=laps, driver=ent.get("driver", ""), car=ent.get("car", ""), cls=ent.get("cls", ""))
    return out


STAT_COLS = [("lap", "Круг", 44, "center"), ("time", "Время", 82, "e"), ("s1", "S1", 66, "e"), ("s2", "S2", 66, "e"),
             ("s3", "S3", 66, "e"), ("fuel", "Топливо, л", 76, "e"), ("left", "Остаток, л", 76, "e"),
             ("tyres", "Остаток шин, % (ЛП ПП ЛЗ ПЗ)", 190, "center"), ("wear", "Износ за круг, %", 170, "center"), ("vmax", "Vmax", 58, "e"), ("vmin", "Vmin", 58, "e"), ("pit", "Пит", 34, "center")]


def lap_row(x):
    ty = x.get("tyres") or [None] * 4
    used = x.get("tyre_used")
    return [x.get("lap", ""), fmt_time(x.get("time")), fmt_time(x.get("s1"), True), fmt_time(x.get("s2"), True),
            fmt_time(x.get("s3"), True), fmt_num(x.get("fuel_used"), 2), fmt_num(x.get("fuel_left"), 1)] + \
           [" ".join(f"{v * 100:.0f}" for v in ty) if all(isinstance(v, (int, float)) for v in ty) else "—",
            " ".join(f"{v * 100:.1f}" for v in used) if used and len(used) == 4 else "—",
            fmt_num(x.get("vmax"), 0), fmt_num(x.get("vmin"), 0),
            "●" if x.get("pit") else ""]


def _ru(v):
    """Для CSV: десятичная запятая, чтобы Google Таблицы и Excel с русской локалью сразу видели числа."""
    return str(v).replace(".", ",") if isinstance(v, (int, float, str)) else v


def export_csv(path, d):
    import csv
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow([session_title(d), d.get("car", ""), d.get("started", "")])
        w.writerow([n for _, n, _, _ in STAT_COLS])
        for x in d.get("laps", []):
            w.writerow([_ru(v) for v in lap_row(x)])


def export_all_csv(path, sessions):
    """Все сохранённые сессии в одной плоской таблице (удобно для сводных таблиц и графиков)."""
    import csv
    head = ["Начало", "Игра", "Трасса", "Сессия", "Машина", "Класс"] + [n for _, n, _, _ in STAT_COLS]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(head)
        for _, d in sessions:
            pre = [d.get("started", ""), d.get("game", ""), d.get("track", ""), d.get("label") or d.get("kind", ""),
                   d.get("car", ""), d.get("cls", "")]
            for x in d.get("laps", []):
                if isinstance(x, dict):
                    w.writerow(pre + [_ru(v) for v in lap_row(x)])


# ---------------------------------------------------------------------------
# телеметрия: чтение кругов, экспорт в MoTeC .ld и CSV, просмотр графиков
# ---------------------------------------------------------------------------
TEL_DIR = os.path.join(STATS_DIR, "tel")
TEL_COLS = ("t", "d", "spd", "thr", "brk", "clu", "str", "gear", "rpm", "x", "z", "fuel", "wat", "oil",
            "tt0", "tt1", "tt2", "tt3", "tp0", "tp1", "tp2", "tp3", "bt0", "bt1", "bt2", "bt3")
WHEELS_ = ("FL", "FR", "RL", "RR")
LD_CHANNELS = [("spd", "Ground Speed", "Speed", "km/h", 1.0), ("thr", "Throttle Pos", "Throttle", "%", 100.0),
               ("brk", "Brake Pos", "Brake", "%", 100.0), ("clu", "Clutch Pos", "Clutch", "%", 100.0),
               ("str", "Steering Pos", "Steer", "%", 100.0), ("gear", "Gear", "Gear", "", 1.0),
               ("rpm", "Engine RPM", "RPM", "rpm", 1.0), ("fuel", "Fuel Level", "Fuel", "l", 1.0),
               ("wat", "Water Temp", "WaterT", "C", 1.0), ("oil", "Oil Temp", "OilT", "C", 1.0),
               ("x", "Pos X", "PosX", "m", 1.0), ("z", "Pos Z", "PosZ", "m", 1.0), ("d", "Lap Distance", "LapDist", "m", 1.0)]
for _i, _w in enumerate(WHEELS_):
    LD_CHANNELS += [(f"tt{_i}", f"Tyre Temp {_w}", f"TT {_w}", "C", 1.0), (f"tp{_i}", f"Tyre Press {_w}", f"TP {_w}", "kPa", 1.0),
                    (f"bt{_i}", f"Brake Temp {_w}", f"BT {_w}", "C", 1.0)]


def tel_folder(session_path):
    return os.path.join(TEL_DIR, os.path.splitext(os.path.basename(session_path))[0])


def tel_files(session_path):
    """[(номер круга, путь)] записанных кругов сессии."""
    out = []
    try:
        for n in os.listdir(tel_folder(session_path)):
            m = re.match(r"lap(\d+)\.json\.gz$", n)
            if m:
                out.append((int(m.group(1)), os.path.join(tel_folder(session_path), n)))
    except OSError:
        pass
    return sorted(out)


class TLap:
    """Один круг телеметрии: ряды по дистанции, значение в любой точке — линейной интерполяцией."""

    def __init__(self, raw):
        self.lap = raw.get("lap")
        self.time = raw.get("time")
        self.pit = bool(raw.get("pit"))
        self.partial = bool(raw.get("partial"))
        cols = raw.get("cols", {})
        n = min((len(cols.get(k, [])) for k in TEL_COLS), default=0)
        self.n = n
        self.c = {k: list(cols.get(k, []))[:n] for k in TEL_COLS}
        m, mono = -1.0, []
        for v in self.c["d"]:
            m = max(m, v)
            mono.append(m)
        self.c["d"] = mono
        self.c["t"] = [max(0.0, v) for v in self.c["t"]]
        self.length = mono[-1] if mono else 0.0

    def at(self, col, d):
        ds = self.c["d"]
        if not ds:
            return None
        i = bisect.bisect_right(ds, d)
        ys = self.c[col]
        if i <= 0:
            return ys[0]
        if i >= self.n:
            return ys[-1]
        if col == "gear":
            return ys[i - 1]
        d0, d1 = ds[i - 1], ds[i]
        if d1 - d0 < 1e-6:
            return ys[i]
        k = (d - d0) / (d1 - d0)
        return ys[i - 1] + (ys[i] - ys[i - 1]) * k


_tel_cache = {}


def load_tlap(fp):
    try:
        mt = os.path.getmtime(fp)
    except OSError:
        return None
    hit = _tel_cache.get(fp)
    if hit and hit[0] == mt:
        return hit[1]
    try:
        with gzip.open(fp, "rt", encoding="utf-8") as f:
            lap = TLap(json.load(f))
    except (OSError, ValueError, EOFError):
        return None
    if len(_tel_cache) > 12:
        _tel_cache.pop(next(iter(_tel_cache)))
    _tel_cache[fp] = (mt, lap)
    return lap


def tel_lap_info(fp):
    """Время круга и признаки без разбора всех рядов (читаем заголовок: json небольшой)."""
    lap = load_tlap(fp)
    return lap


def resample_session(session_path, hz=20.0, progress=None):
    """Склеивает круги сессии в непрерывные ряды с равным шагом: {имя: [значения]}, число точек."""
    files = tel_files(session_path)
    out = {k: [] for k in [c[0] for c in LD_CHANNELS] + ["lapno"]}
    dt = 1.0 / hz
    for fi, (no, fp) in enumerate(files):
        lap = load_tlap(fp)
        if progress:
            progress(fi, len(files))
        if lap is None or lap.n < 10 or lap.partial:
            continue
        ts = lap.c["t"]
        total = ts[-1]
        j, k = 0, 0.0
        steps = int(total / dt)
        for s in range(steps):
            t = s * dt
            while j + 1 < lap.n and ts[j + 1] <= t:
                j += 1
            if j + 1 < lap.n and ts[j + 1] > ts[j]:
                f = (t - ts[j]) / (ts[j + 1] - ts[j])
            else:
                f = 0.0
            for key, _, _, _, _ in LD_CHANNELS:
                ys = lap.c[key]
                out[key].append(ys[j] if key == "gear" or j + 1 >= lap.n else ys[j] + (ys[j + 1] - ys[j]) * f)
            out["lapno"].append(float(no))
    return out


def write_ld(path, data, hz, driver="", vehicle="", venue="", comment="Yassno overlay", when=None):
    """Файл MoTeC .ld (формат по данным открытого разбора ldparser): все каналы float32 с одной частотой."""
    import datetime
    when = when or datetime.datetime.now()
    head_fmt = "<" + ("I4x" "II" "20x" "I" "24x" "HHH" "I" "8s" "H" "H" "I" "4x" "16s" "16x" "16s" "16x" "64s" "64s" "64x" "64s" "64x" "1024x" "I" "66x" "64s" "126x")
    ev_fmt = "<64s64s1024sH"
    ch_fmt = "<" + ("IIII" "H" "HHH" "hhhh" "32s" "8s" "12s" "40x")
    chans = [(nm, sh, un, data[key], sc) for key, nm, sh, un, sc in LD_CHANNELS if data.get(key)]
    chans.append(("Lap Number", "Lap", "", data["lapno"], 1.0))
    head_sz, ev_sz, ch_sz = struct.calcsize(head_fmt), struct.calcsize(ev_fmt), struct.calcsize(ch_fmt)
    meta_ptr = head_sz + ev_sz
    data_ptr = meta_ptr + len(chans) * ch_sz
    enc = lambda s, n: str(s).encode("ascii", "replace")[:n]
    with open(path, "wb") as f:
        f.write(struct.pack(head_fmt, 0x40, meta_ptr, data_ptr, head_sz, 1, 0x4240, 0xf, 0x1f44, b"ADL", 420, 0xadb0, len(chans),
                            when.strftime("%d/%m/%Y").encode(), when.strftime("%H:%M:%S").encode(),
                            enc(driver, 63), enc(vehicle, 63), enc(venue, 63), 0xc81a4, enc(comment, 63)))
        f.write(struct.pack(ev_fmt, enc(venue, 63), enc("session", 63), enc(comment, 1023), 0))
        ptr, prev = data_ptr, 0
        for i, (nm, sh, un, vals, sc) in enumerate(chans):
            nxt = meta_ptr + (i + 1) * ch_sz if i < len(chans) - 1 else 0
            f.write(struct.pack(ch_fmt, prev, nxt, ptr, len(vals), 0x2ee1 + i, 0x07, 4, int(hz), 0, 1, 1, 0,
                                enc(nm, 31), enc(sh, 7), enc(un, 11)))
            prev = meta_ptr + i * ch_sz
            ptr += 4 * len(vals)
        for nm, sh, un, vals, sc in chans:
            arr = array.array("f", [v * sc for v in vals])
            if sys.byteorder == "big":
                arr.byteswap()
            arr.tofile(f)


def export_lap_csv(path, lap):
    import csv
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(TEL_COLS)
        for i in range(lap.n):
            w.writerow([lap.c[k][i] for k in TEL_COLS])


STRIPS = [("spd", "Скорость, км/ч", [("spd", "", None, 1.0)], None),
          ("pedals", "Газ и тормоз, %", [("thr", "газ", "#2ee26d", 100.0), ("brk", "тормоз", "#ff3b30", 100.0)], (0.0, 100.0)),
          ("steer", "Руль, %", [("str", "", None, 100.0)], (-100.0, 100.0)),
          ("gear", "Передача", [("gear", "", None, 1.0)], None),
          ("rpm", "Обороты, rpm", [("rpm", "", None, 1.0)], None),
          ("delta", "Дельта B − A, с", [], None),
          ("tyret", "Температура шин, °C", [(f"tt{i}", w, c, 1.0) for i, (w, c) in enumerate(zip(WHEELS_, ("#ff5a4f", "#4da3ff", "#ffc107", "#2ee26d")))], None),
          ("tyrep", "Давление шин, кПа", [(f"tp{i}", w, c, 1.0) for i, (w, c) in enumerate(zip(WHEELS_, ("#ff5a4f", "#4da3ff", "#ffc107", "#2ee26d")))], None),
          ("braket", "Температура тормозов, °C", [(f"bt{i}", w, c, 1.0) for i, (w, c) in enumerate(zip(WHEELS_, ("#ff5a4f", "#4da3ff", "#ffc107", "#2ee26d")))], None),
          ("fuel", "Топливо, л", [("fuel", "", None, 1.0)], None),
          ("temps", "Вода и масло, °C", [("wat", "вода", "#4da3ff", 1.0), ("oil", "масло", "#ffc107", 1.0)], None)]
STRIP_DEFAULT = ["spd", "pedals", "steer", "gear", "rpm", "delta"]
COL_A, COL_B = "#4da3ff", "#ff9f1a"


def nice_step(span, target=8):
    raw = span / max(1, target)
    for s in (10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 2500, 5000):
        if s >= raw:
            return s
    return 10000


def speed_color(v, lo, hi):
    f = 0.0 if hi <= lo else max(0.0, min(1.0, (v - lo) / (hi - lo)))
    stops = [(0.0, (60, 90, 255)), (0.35, (40, 200, 120)), (0.7, (255, 200, 40)), (1.0, (255, 70, 60))]
    for (a, ca), (b, cb) in zip(stops, stops[1:]):
        if f <= b:
            k = (f - a) / (b - a)
            return "#%02x%02x%02x" % tuple(int(ca[i] + (cb[i] - ca[i]) * k) for i in range(3))
    return "#ff463c"


# ---- загрузка файлов телеметрии с компьютера ------------------------------------------------
def read_ld(path):
    """Читает .ld MoTeC (формат по описанию ldparser): (шапка, [(имя, единицы, частота, значения)])."""
    with open(path, "rb") as f:
        buf = f.read()
    head_fmt = "<" + ("I4x" "II" "20x" "I" "24x" "HHH" "I" "8s" "H" "H" "I" "4x" "16s" "16x" "16s" "16x" "64s" "64s" "64x" "64s" "64x" "1024x" "I" "66x" "64s" "126x")
    h = struct.unpack_from(head_fmt, buf, 0)
    dec = lambda b: b.split(b"\0")[0].decode("latin-1", "replace").strip()
    info = {"date": dec(h[12]), "time": dec(h[13]), "driver": dec(h[14]), "vehicle": dec(h[15]), "venue": dec(h[16])}
    ch_fmt = "<" + ("IIII" "H" "HHH" "hhhh" "32s" "8s" "12s" "40x")
    ptr, out, guard = h[1], [], 0
    while ptr and guard < 2000 and ptr + struct.calcsize(ch_fmt) <= len(buf):
        guard += 1
        _, nxt, dptr, dlen, _, dta, dtb, freq, shift, mul, scale, dp, name, short, unit = struct.unpack_from(ch_fmt, buf, ptr)
        code = {(7, 2): "e", (7, 4): "f", (0, 2): "h", (3, 2): "h", (5, 4): "i", (3, 4): "i", (0, 4): "i", (8, 8): "d"}.get((dta, dtb))
        if code and freq > 0 and 0 < dlen < 50_000_000 and dptr + dlen * struct.calcsize(code) <= len(buf):
            raw = struct.unpack_from("<%d%s" % (dlen, code), buf, dptr)
            k = 10.0 ** (-dp)
            sc = scale if scale else 1
            vals = [(v / sc * k + shift) * mul for v in raw]
            out.append((dec(name), dec(unit), int(freq), vals))
        ptr = nxt
    return info, out


def _guess_channels(chans):
    """Сопоставляет каналы файла с нашими: {наш ключ: (единицы, частота, значения, имя)}."""
    found = {}
    wheel_of = lambda n: next((i for i, keys in enumerate((("fl", "lf", "front left"), ("fr", "rf", "front right"),
                                                           ("rl", "lr", "rear left"), ("rr", "rear right"))) if any(re.search(r"\b" + k + r"\b|_" + k + r"\b|\b" + k + "_", n) for k in keys)), None)
    for name, unit, hz, vals in chans:
        n = name.lower()
        key = None
        if re.search(r"gps|latitude|longitude", n):
            key = "lat" if "lat" in n else "lon" if "lon" in n else None
        elif re.search(r"tyre|tire", n) and "temp" in n and wheel_of(n) is not None:
            key = f"tt{wheel_of(n)}"
        elif re.search(r"tyre|tire", n) and "press" in n and wheel_of(n) is not None:
            key = f"tp{wheel_of(n)}"
        elif "brake" in n and "temp" in n and wheel_of(n) is not None:
            key = f"bt{wheel_of(n)}"
        elif "lap" in n and ("dist" in n):
            key = "d"
        elif re.fullmatch(r"(lap|lap number|lap count|laps)", n):
            key = "lapno"
        elif "speed" in n and "wheel" not in n and "engine" not in n:
            key = "spd"
        elif "throttle" in n:
            key = "thr"
        elif "brake" in n and "temp" not in n:
            key = "brk"
        elif "clutch" in n:
            key = "clu"
        elif "steer" in n:
            key = "str"
        elif n in ("gear", "gear pos", "gear position") or n.startswith("gear"):
            key = "gear"
        elif "rpm" in n or "engine speed" in n:
            key = "rpm"
        elif "fuel" in n and ("level" in n or "load" in n):
            key = "fuel"
        elif "water" in n and "temp" in n:
            key = "wat"
        elif "oil" in n and "temp" in n:
            key = "oil"
        elif re.search(r"pos(ition)? ?x", n) or n in ("x", "world x"):
            key = "x"
        elif re.search(r"pos(ition)? ?[zy]", n) or n in ("z", "world z"):
            key = "z"
        if key and key not in found:
            found[key] = (unit, hz, vals, name)
    return found


def _resample(vals, hz, n_out, hz_out, nearest=False):
    out = []
    for i in range(n_out):
        t = i / hz_out * hz
        j = int(t)
        if j >= len(vals) - 1:
            out.append(vals[-1])
        elif nearest:
            out.append(vals[j])
        else:
            out.append(vals[j] + (vals[j + 1] - vals[j]) * (t - j))
    return out


def import_ld_file(path, stats_dir=None, tel_dir=None):
    """Файл .ld -> новая сессия в статистике: делит на круги по каналу номера круга, дистанцию считает сама при необходимости."""
    import datetime
    stats_dir, tel_dir = stats_dir or STATS_DIR, tel_dir or TEL_DIR
    info, chans = read_ld(path)
    found = _guess_channels(chans)
    if "spd" not in found and not found:
        raise ValueError("В файле нет знакомых каналов (скорость, педали, обороты).")
    hz_out = 20.0
    dur = max(len(v[2]) / v[1] for v in found.values())
    n = int(dur * hz_out)
    if n < 40:
        raise ValueError("Файл слишком короткий.")
    cols = {k: [0.0] * n for k in TEL_COLS}
    cols["t"] = [i / hz_out for i in range(n)]
    for key, (unit, hz, vals, name) in found.items():
        if key in ("lapno", "lat", "lon"):
            continue
        rs = _resample(vals, hz, n, hz_out, nearest=(key == "gear"))
        u = unit.lower()
        if key == "spd":
            mx = max(rs) if rs else 0
            rs = [v * 3.6 for v in rs] if ("m/s" in u or (u == "" and mx < 120 and False)) else [v * 1.609344 for v in rs] if "mph" in u else rs
        elif key in ("thr", "brk", "clu"):
            mx = max(rs) if rs else 0
            rs = [v / (100.0 if mx > 1.5 else 1.0) for v in rs]
        elif key == "str":
            mx = max((abs(v) for v in rs), default=1.0) or 1.0
            rs = [v / mx for v in rs]
        elif key in ("tt0", "tt1", "tt2", "tt3", "bt0", "bt1", "bt2", "bt3", "wat", "oil") and "k" == u:
            rs = [v - 273.15 for v in rs]
        cols[key] = rs
    if "lat" in found and "lon" in found and not any(cols["x"]):
        la = _resample(found["lat"][2], found["lat"][1], n, hz_out)
        lo = _resample(found["lon"][2], found["lon"][1], n, hz_out)
        cols["x"] = [(b - lo[0]) * 111320.0 * math.cos(math.radians(la[0])) for b in lo]
        cols["z"] = [(a - la[0]) * 110540.0 for a in la]
    laps = [(0, n)]
    if "lapno" in found:
        ln = _resample(found["lapno"][2], found["lapno"][1], n, hz_out, nearest=True)
        laps, st = [], 0
        for i in range(1, n):
            if int(ln[i]) != int(ln[i - 1]):
                laps.append((st, i))
                st = i
        laps.append((st, n))
        laps = [(a, b) for a, b in laps if b - a >= 40]
        if not laps:
            laps = [(0, n)]
    base = os.path.splitext(os.path.basename(path))[0]
    slug = "".join(ch if ch.isalnum() else "_" for ch in base)[:40].strip("_") or "import"
    now = datetime.datetime.now()
    stem = f"import_{now.strftime('%Y%m%d-%H%M%S')}_{slug}"
    folder = os.path.join(tel_dir, stem)
    os.makedirs(folder, exist_ok=True)
    recs = []
    for li, (a, b) in enumerate(laps, 1):
        seg = {k: cols[k][a:b] for k in TEL_COLS}
        t0 = seg["t"][0]
        seg["t"] = [round(v - t0, 2) for v in seg["t"]]
        if "d" in found and any(cols["d"][a:b]):
            seg["d"] = [round(v, 1) for v in seg["d"]]
        else:
            dist, acc = [], 0.0
            for v in seg["spd"]:
                dist.append(round(acc, 1))
                acc += v / 3.6 / hz_out
            seg["d"] = dist
        for k in TEL_COLS:
            if k not in ("t", "d"):
                seg[k] = [round(v, 3) for v in seg[k]]
        dt_ = seg["t"][-1]
        with gzip.open(os.path.join(folder, f"lap{li:03d}.json.gz"), "wt", encoding="utf-8") as f:
            json.dump({"v": 1, "lap": li, "time": round(dt_, 3), "pit": False, "partial": False, "hz": hz_out, "cols": seg}, f, separators=(",", ":"))
        recs.append({"lap": li, "time": round(dt_, 3), "s1": None, "s2": None, "s3": None, "fuel_used": None, "fuel_left": None,
                     "tyres": None, "tyre_used": None, "vmax": max(seg["spd"]) or None, "vmin": None, "pit": False, "at": ""})
    data = {"version": 1, "game": "import", "track": info.get("venue") or base, "kind": "IMPORT", "label": tr("Импорт"), "car": info.get("vehicle", ""),
            "model": "", "cls": "", "driver": info.get("driver", ""), "track_len": 0.0, "started": now.strftime("%Y-%m-%d %H:%M:%S"),
            "laps": recs, "results": [], "imported": base}
    os.makedirs(stats_dir, exist_ok=True)
    spath = os.path.join(stats_dir, stem + ".json")
    write_json(spath, data)
    return spath


def import_lap_files(paths, stats_dir=None, tel_dir=None):
    """Файлы кругов нашего формата (.json.gz) или CSV-экспорт круга -> новая сессия."""
    import csv
    import datetime
    stats_dir, tel_dir = stats_dir or STATS_DIR, tel_dir or TEL_DIR
    laps = []
    for p in paths:
        if p.lower().endswith(".gz"):
            with gzip.open(p, "rt", encoding="utf-8") as f:
                laps.append(json.load(f))
        else:
            with open(p, newline="", encoding="utf-8-sig") as f:
                rows = list(csv.reader(f, delimiter=";"))
            names, body = rows[0], rows[1:]
            cols = {k: [] for k in TEL_COLS}
            for r in body:
                for k, v in zip(names, r):
                    if k in cols:
                        try:
                            cols[k].append(float(v.replace(",", ".")))
                        except ValueError:
                            cols[k].append(0.0)
            m = re.search(r"(\d+)", os.path.basename(p))
            laps.append({"v": 1, "lap": int(m.group(1)) if m else len(laps) + 1, "time": (cols["t"][-1] if cols["t"] else None),
                         "pit": False, "partial": False, "hz": 20.0, "cols": cols})
    laps = [x for x in laps if isinstance(x, dict) and isinstance(x.get("cols"), dict) and x["cols"].get("t")]
    if not laps:
        raise ValueError("В выбранных файлах нет данных телеметрии.")
    laps.sort(key=lambda x: x.get("lap") or 0)
    now = datetime.datetime.now()
    stem = f"import_{now.strftime('%Y%m%d-%H%M%S')}_laps"
    folder = os.path.join(tel_dir, stem)
    os.makedirs(folder, exist_ok=True)
    recs = []
    for x in laps:
        no = int(x.get("lap") or len(recs) + 1)
        with gzip.open(os.path.join(folder, f"lap{no:03d}.json.gz"), "wt", encoding="utf-8") as f:
            json.dump(x, f, separators=(",", ":"))
        recs.append({"lap": no, "time": x.get("time"), "s1": None, "s2": None, "s3": None, "fuel_used": None, "fuel_left": None,
                     "tyres": None, "tyre_used": None, "vmax": max(x["cols"].get("spd") or [0]) or None, "vmin": None,
                     "pit": bool(x.get("pit")), "at": ""})
    data = {"version": 1, "game": "import", "track": tr("Загруженные круги"), "kind": "IMPORT", "label": tr("Импорт"), "car": "", "model": "",
            "cls": "", "driver": "", "track_len": 0.0, "started": now.strftime("%Y-%m-%d %H:%M:%S"), "laps": recs, "results": [], "imported": True}
    os.makedirs(stats_dir, exist_ok=True)
    spath = os.path.join(stats_dir, stem + ".json")
    write_json(spath, data)
    return spath



class TelemetryView(ttk.Frame):
    """Просмотр телеметрии как в MoTeC: ряды по дистанции с общим курсором, масштаб колесом, сравнение двух кругов."""

    def __init__(self, parent, stats):
        super().__init__(parent)
        self.stats, self.app = stats, stats.app
        self.session = None
        self.files = {}
        self.A = self.B = None
        self.view, self.full, self.cur_d, self._drag = (0.0, 1.0), (0.0, 1.0), None, None
        self.on = [k for k in (self.app.cfg.get("tel_strips") or STRIP_DEFAULT) if k in dict((s[0], 1) for s in STRIPS)] or list(STRIP_DEFAULT)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)
        left = ttk.Frame(self)
        left.grid(row=0, column=0, sticky="ns", padx=(0, 8))
        ttk.Label(left, text="Круги", style="Head.TLabel").pack(anchor="w")
        self.tree = ttk.Treeview(left, columns=("lap", "time", "mark"), show="headings", height=10, selectmode="browse")
        for k, name, w in (("lap", "Круг", 46), ("time", "Время", 80), ("mark", "", 60)):
            self.tree.heading(k, text=tr(name))
            self.tree.column(k, width=w, minwidth=w, anchor="center" if k != "time" else "e", stretch=False)
        self.tree.pack(fill="y", expand=True, pady=(2, 4))
        self.tree.tag_configure("A", foreground=COL_A)
        self.tree.tag_configure("B", foreground=COL_B)
        self.tree.tag_configure("pit", foreground=UI_DIM)
        self.tree.tag_configure("best", foreground="#b04dff")
        self.tree.bind("<ButtonRelease-1>", lambda e: self.pick("A"))
        self.tree.bind("<Button-3>", self.right_pick)
        ttk.Label(left, text=tr("Левая кнопка — круг A,\nправая — круг B для сравнения"), style="Dim.TLabel", justify="left").pack(anchor="w")
        bb = ttk.Frame(left)
        bb.pack(fill="x", pady=(4, 0))
        ttk.Button(bb, text=tr("Лучший → A"), command=self.best_to_a).pack(side="left", padx=(0, 4))
        ttk.Button(bb, text=tr("Без B"), command=self.clear_b).pack(side="left")
        self.map = tk.Canvas(left, width=230, height=190, bg=UI_FIELD, highlightthickness=1, highlightbackground=UI_BORDER)
        self.map.pack(pady=(6, 0))

        right = ttk.Frame(self)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)
        bar = ttk.Frame(right)
        bar.grid(row=0, column=0, sticky="ew")
        self.strip_vars = {}
        mb = ttk.Menubutton(bar, text=tr("Каналы ▾"))
        menu = tk.Menu(mb, tearoff=0)
        mb["menu"] = menu
        for k, name, _, _ in STRIPS:
            v = tk.BooleanVar(value=k in self.on)
            self.strip_vars[k] = v
            menu.add_checkbutton(label=tr(name.split(",")[0]), variable=v, command=self.strips_changed)
        mb.pack(side="left")
        self.legend = ttk.Label(bar, text=tr("Колесо — масштаб, перетаскивание — сдвиг, двойной щелчок — весь круг"), style="Dim.TLabel")
        self.legend.pack(side="left", padx=12)
        self.cv = tk.Canvas(right, bg=UI_FIELD, highlightthickness=1, highlightbackground=UI_BORDER, height=420)
        self.cv.grid(row=1, column=0, sticky="nsew", pady=(4, 0))
        self.cv.bind("<Configure>", lambda e: self.redraw())
        self.cv.bind("<Motion>", self.on_motion)
        self.cv.bind("<Leave>", lambda e: self.clear_cursor())
        self.cv.bind("<MouseWheel>", self.on_wheel)
        self.cv.bind("<Button-4>", lambda e: self.on_wheel(e, 1))
        self.cv.bind("<Button-5>", lambda e: self.on_wheel(e, -1))
        self.cv.bind("<ButtonPress-1>", self.on_press)
        self.cv.bind("<B1-Motion>", self.on_drag)
        self.cv.bind("<Double-Button-1>", lambda e: self.reset_view())
        bot = ttk.Frame(right)
        bot.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        self.msg = ttk.Label(bot, text="", style="Dim.TLabel")
        self.msg.pack(side="left")
        ttk.Button(bot, text=tr("Загрузить файл…"), command=self.load_files).pack(side="left", padx=(0, 10))
        ttk.Button(bot, text=tr("Открыть в i2 Pro"), command=self.open_i2).pack(side="right")
        ttk.Button(bot, text=tr("Экспорт сессии в .ld…"), command=self.export_ld).pack(side="right", padx=4)
        ttk.Button(bot, text=tr("CSV круга A…"), command=self.export_csv).pack(side="right")

    # ---- данные
    def set_session(self, path, d):
        if path == self.session and path is not None:
            n = len(self.items)
            self.refresh_list()
            if len(self.items) != n:
                self.fill_tree()
            if self.A is None and self.items:
                self.set_default_a()
                self.after_select()
            return
        self.session = path
        self.A = self.B = None
        self.refresh_list()
        self.set_default_a()
        self.fill_tree()
        self.after_select()

    def set_default_a(self):
        best = None
        for no, fp, lap in self.items:
            if lap and lap.time and not lap.pit and not lap.partial and (best is None or lap.time < best[1].time):
                best = (fp, lap)
        first = next(((fp, lap) for no, fp, lap in self.items if lap and not lap.partial), None)
        pick = best or first
        if pick:
            self.A = pick[0]

    def refresh_list(self):
        self.items = []
        for no, fp in (tel_files(self.session) if self.session else []):
            self.items.append((no, fp, load_tlap(fp)))
        self.files = {fp: no for no, fp, _ in self.items}
        self.fill_tree()

    def fill_tree(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        times = [l.time for _, _, l in self.items if l and l.time and not l.pit and not l.partial]
        best = min(times) if times else None
        for no, fp, lap in self.items:
            tags = []
            if fp == self.A:
                tags.append("A")
            elif fp == self.B:
                tags.append("B")
            elif lap and lap.pit:
                tags.append("pit")
            elif lap and lap.time and lap.time == best:
                tags.append("best")
            mark = "A" if fp == self.A else "B" if fp == self.B else (tr("пит") if lap and lap.pit else tr("часть") if lap and lap.partial else "")
            self.tree.insert("", "end", iid=fp, values=(no, fmt_time(lap.time) if lap and lap.time else "-", mark), tags=tags)

    def pick(self, which):
        sel = self.tree.selection()
        if not sel:
            return
        if which == "A":
            self.A = sel[0]
            if self.B == self.A:
                self.B = None
        else:
            self.B = None if sel[0] == self.A else sel[0]
        self.fill_tree()
        self.after_select()

    def right_pick(self, e):
        iid = self.tree.identify_row(e.y)
        if iid:
            self.tree.selection_set(iid)
            self.pick("B")

    def best_to_a(self):
        cand = [(l.time, fp) for _, fp, l in self.items if l and l.time and not l.pit and not l.partial]
        if cand:
            self.A = min(cand)[1]
            if self.B == self.A:
                self.B = None
            self.fill_tree()
            self.after_select()

    def clear_b(self):
        self.B = None
        self.fill_tree()
        self.after_select()

    def lap(self, fp):
        return load_tlap(fp) if fp else None

    def after_select(self):
        a = self.lap(self.A)
        b = self.lap(self.B)
        ln = max((x.length for x in (a, b) if x), default=0.0)
        self.full = (0.0, max(100.0, ln))
        self.view = self.full
        self.cur_d = None
        self.draw_map()
        self.redraw()

    def strips_changed(self):
        self.on = [k for k, _, _, _ in STRIPS if self.strip_vars[k].get()] or ["spd"]
        self.app.cfg["tel_strips"] = list(self.on)
        self.app.save_cfg()
        self.redraw()

    # ---- карта
    def draw_map(self):
        m = self.map
        m.delete("all")
        a = self.lap(self.A)
        self.mapxf = None
        if not a or a.n < 10:
            m.create_text(115, 95, text=tr("Нет данных"), fill=UI_DIM)
            return
        xs, zs = a.c["x"], a.c["z"]
        pts = [(x, z, v) for x, z, v in zip(xs, zs, a.c["spd"]) if x or z]
        if len(pts) < 5:
            return
        x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
        z0, z1 = min(p[1] for p in pts), max(p[1] for p in pts)
        W, H, pad = 230, 190, 12
        k = min((W - 2 * pad) / max(1.0, x1 - x0), (H - 2 * pad) / max(1.0, z1 - z0))
        ox, oy = (W - (x1 - x0) * k) / 2, (H - (z1 - z0) * k) / 2
        self.mapxf = lambda x, z: (ox + (x - x0) * k, oy + (z - z0) * k)
        vlo, vhi = min(p[2] for p in pts), max(p[2] for p in pts)
        step = max(1, len(pts) // 400)
        for i in range(0, len(pts) - step, step):
            p, q = pts[i], pts[i + step]
            ax, ay = self.mapxf(p[0], p[1])
            bx, by = self.mapxf(q[0], q[1])
            m.create_line(ax, ay, bx, by, fill=speed_color(p[2], vlo, vhi), width=3, capstyle="round")

    # ---- график
    def active_strips(self):
        return [s for s in STRIPS if s[0] in self.on and (s[0] != "delta" or (self.A and self.B))]

    def redraw(self):
        c = self.cv
        c.delete("all")
        W, H = max(300, c.winfo_width()), max(160, c.winfo_height())
        a, b = self.lap(self.A), self.lap(self.B)
        self.geom = None
        if not a or a.n < 10:
            msg = tr("Телеметрии этой сессии пока нет.") if not self.items else tr("Выберите круг слева.")
            c.create_text(W / 2, H / 2 - 12, text=msg, fill=UI_FG, font=("Segoe UI", 11))
            if not self.items:
                c.create_text(W / 2, H / 2 + 14, text=tr("Она пишется оверлеем в папку stats/tel, пока включён переключатель в настройках лаунчера."),
                              fill=UI_DIM, font=("Segoe UI", 9))
            return
        strips = self.active_strips()
        L, R, T, Bm = 64, 12, 20, 22
        sh = (H - T - Bm) / max(1, len(strips))
        x0, x1 = self.view
        px = lambda d: L + (W - L - R) * (d - x0) / max(1e-6, x1 - x0)
        self.geom = (L, R, T, Bm, sh, strips, W, H, px)
        step = nice_step(x1 - x0)
        d = math.ceil(x0 / step) * step
        grid_x = []
        while d <= x1:
            grid_x.append(d)
            d += step
        ndraw = int(W * 1.2)
        for i, (key, title, series, fixed) in enumerate(strips):
            y0, y1 = T + i * sh, T + (i + 1) * sh - 4
            c.create_rectangle(L, y0, W - R, y1, fill=UI_BG, outline=UI_BORDER)
            for gx in grid_x:
                c.create_line(px(gx), y0, px(gx), y1, fill=UI_BORDER)
            curves = self.strip_curves(key, series, a, b)
            vals = [v for cv_ in curves for v in cv_[3]]
            if fixed:
                lo, hi = fixed
            elif vals:
                lo, hi = min(vals), max(vals)
                if key == "gear":
                    lo, hi = 0, max(hi, 1) + 0.5
                elif key == "delta":
                    m = max(abs(lo), abs(hi), 0.05)
                    lo, hi = -m, m
                else:
                    pad = max((hi - lo) * 0.08, 0.5)
                    lo, hi = lo - pad, hi + pad
            else:
                lo, hi = 0.0, 1.0
            self.strip_range = getattr(self, "strip_range", {})
            self.strip_range[key] = (lo, hi)
            py = lambda v, y0=y0, y1=y1, lo=lo, hi=hi: y1 - (y1 - y0) * (v - lo) / max(1e-9, hi - lo)
            mid = (lo + hi) / 2
            c.create_line(L, py(mid), W - R, py(mid), fill=UI_BORDER, dash=(2, 4))
            for v, yy, an in ((hi, y0 + 7, "e"), (lo, y1 - 7, "e")):
                c.create_text(L - 5, yy, text=f"{v:.0f}" if abs(hi - lo) > 8 else f"{v:.2f}", fill=UI_DIM, anchor=an, font=("Segoe UI", 8))
            if key == "delta":
                c.create_line(L, py(0), W - R, py(0), fill=UI_DIM)
            for dist, ys, col, vv, dashed in [(cv_[0], cv_[1], cv_[2], cv_[3], cv_[4]) for cv_ in curves]:
                self.poly(c, dist, ys, px, py, x0, x1, ndraw, col, dashed)
        for gx in grid_x:
            c.create_text(px(gx), H - 9, text=f"{gx:.0f}", fill=UI_DIM, font=("Segoe UI", 8))
        c.create_text(W - R, H - 9, text="м", fill=UI_DIM, anchor="e", font=("Segoe UI", 8))
        self.draw_cursor()

    def strip_curves(self, key, series, a, b):
        """[(дистанции, значения (экранные), цвет, значения для масштаба, штрих)]"""
        out = []
        if key == "delta":
            if a and b:
                ds, ys = [], []
                for i in range(0, a.n, max(1, a.n // 800)):
                    dd = a.c["d"][i]
                    tb = b.at("t", dd)
                    if tb is not None:
                        ds.append(dd)
                        ys.append(tb - a.c["t"][i])
                out.append((ds, ys, "#ff5a4f", ys, False))
            return out
        for col, label, color, sc in series:
            for lap, dashed, defcol in ((a, False, COL_A), (b, True, COL_B)):
                if not lap:
                    continue
                if len(series) == 1:
                    ccol, dashed_ = defcol, False
                else:
                    ccol, dashed_ = color, dashed
                ys = [v * sc for v in lap.c[col]]
                out.append((lap.c["d"], ys, ccol, ys, dashed_))
        return out

    def poly(self, c, ds, ys, px, py, x0, x1, ndraw, col, dashed):
        if not ds:
            return
        i0 = max(0, bisect.bisect_left(ds, x0) - 1)
        i1 = min(len(ds), bisect.bisect_right(ds, x1) + 1)
        step = max(1, (i1 - i0) // ndraw)
        pts = []
        for i in range(i0, i1, step):
            pts += [px(ds[i]), py(ys[i])]
        if len(pts) >= 4:
            c.create_line(*pts, fill=col, width=1.6, dash=(4, 3) if dashed else ())

    # ---- курсор, масштаб, сдвиг
    def clear_cursor(self):
        self.cur_d = None
        self.draw_cursor()

    def draw_cursor(self):
        c = self.cv
        c.delete("cur")
        self.map.delete("mapcur")
        if self.geom is None:
            return
        L, R, T, Bm, sh, strips, W, H, px = self.geom
        a, b = self.lap(self.A), self.lap(self.B)
        d = self.cur_d
        x = px(d) if d is not None else -1
        if d is None or not (L <= x <= W - R):
            for i, (key, title, series, fixed) in enumerate(strips):
                c.create_text(L + 8, T + i * sh + 8, text=tr(title), fill=UI_DIM, anchor="w", font=("Segoe UI", 8), tags="cur")
            return
        c.create_line(x, T, x, H - Bm, fill=UI_FG, tags="cur")
        for i, (key, title, series, fixed) in enumerate(strips):
            y0 = T + i * sh
            parts = []
            if key == "delta":
                tb = b.at("t", d) if b else None
                ta = a.at("t", d) if a else None
                if tb is not None and ta is not None:
                    parts.append(f"{tb - ta:+.3f}")
            else:
                for col, label, color, sc in series:
                    for lap, tag in ((a, "A"), (b, "B")):
                        if lap:
                            v = lap.at(col, d)
                            if v is not None:
                                s = f"{v * sc:.0f}" if col in ("spd", "rpm", "gear", "thr", "brk", "str") or abs(v * sc) >= 100 else f"{v * sc:.1f}"
                                parts.append((label + " " if label else "") + s + (tag.lower() if False else ""))
            txt = tr(title.split(",")[0]) + ":  " + "  |  ".join(parts)
            c.create_text(L + 8, y0 + 8, text=txt, fill=UI_FG, anchor="w", font=("Segoe UI Semibold", 9), tags="cur")
        ta = a.at("t", d) if a else None
        tb = b.at("t", d) if b else None
        info = f"{d:.0f} м"
        if ta is not None:
            info += f"   A {ta:.2f} с"
        if tb is not None:
            info += f"   B {tb:.2f} с"
        c.create_text(L, 9, text=info, fill=UI_ACCENT, anchor="w", font=("Segoe UI Semibold", 9), tags="cur")
        if self.mapxf:
            for lap, col in ((a, COL_A), (b, COL_B)):
                if lap:
                    mx, mz = lap.at("x", d), lap.at("z", d)
                    if mx is not None:
                        qx, qy = self.mapxf(mx, mz)
                        self.map.create_oval(qx - 5, qy - 5, qx + 5, qy + 5, fill=col, outline="#ffffff", tags="mapcur")

    def on_motion(self, e):
        if self.geom is None or self._drag:
            return
        L, R, T, Bm, sh, strips, W, H, px = self.geom
        x0, x1 = self.view
        if e.x < L or e.x > W - R:
            return
        self.cur_d = x0 + (x1 - x0) * (e.x - L) / max(1, W - L - R)
        self.draw_cursor()

    def on_wheel(self, e, direction=None):
        if self.geom is None:
            return
        up = (direction > 0) if direction is not None else e.delta > 0
        L, R, T, Bm, sh, strips, W, H, px = self.geom
        x0, x1 = self.view
        f = min(1.0, max(0.0, (e.x - L) / max(1, W - L - R)))
        pivot = x0 + (x1 - x0) * f
        k = 0.8 if up else 1.25
        n0, n1 = pivot - (pivot - x0) * k, pivot + (x1 - pivot) * k
        lo, hi = self.full
        if n1 - n0 >= hi - lo:
            n0, n1 = lo, hi
        elif n1 - n0 < 40:
            return
        else:
            if n0 < lo:
                n0, n1 = lo, lo + (n1 - n0)
            if n1 > hi:
                n0, n1 = hi - (n1 - n0), hi
        self.view = (n0, n1)
        self.redraw()

    def on_press(self, e):
        self._drag = (e.x, self.view)

    def on_drag(self, e):
        if not self._drag or self.geom is None:
            return
        L, R, T, Bm, sh, strips, W, H, px = self.geom
        sx, (v0, v1) = self._drag
        span = v1 - v0
        dd = -(e.x - sx) * span / max(1, W - L - R)
        lo, hi = self.full
        n0, n1 = v0 + dd, v1 + dd
        if n0 < lo:
            n0, n1 = lo, lo + span
        if n1 > hi:
            n0, n1 = hi - span, hi
        self.view = (n0, n1)
        self.redraw()

    def reset_view(self):
        self._drag = None
        self.view = self.full
        self.redraw()

    # ---- экспорт и i2 Pro
    def load_files(self):
        paths = filedialog.askopenfilenames(parent=self, title=tr("Файлы телеметрии"),
                                            filetypes=[(tr("Телеметрия"), "*.ld *.gz *.csv"), ("MoTeC .ld", "*.ld"),
                                                       (tr("Круги оверлея"), "*.gz *.csv"), (tr("Все файлы"), "*.*")])
        if not paths:
            return
        try:
            lds = [p for p in paths if p.lower().endswith(".ld")]
            made = [import_ld_file(p) for p in lds]
            others = [p for p in paths if not p.lower().endswith(".ld")]
            if others:
                made.append(import_lap_files(others))
        except Exception as e:
            mb_error("Не удалось загрузить", str(e))
            return
        self.stats.reload(keep=False)
        if made:
            idx = next((i for i, (pp, _) in enumerate(self.stats.sessions) if pp == made[-1]), 0)
            self.stats.lst.selection_clear(0, "end")
            self.stats.lst.selection_set(idx)
            self.stats.on_select()
        self.msg.config(text=tr("Загружено сессий: ") + str(len(made)))

    def export_csv(self):
        a = self.lap(self.A)
        if not a:
            return
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".csv", initialdir=tel_folder(self.session),
                                            initialfile=f"lap{a.lap:03d}.csv", filetypes=[("CSV", "*.csv")])
        if path:
            try:
                export_lap_csv(path, a)
                self.msg.config(text=tr("Сохранено: ") + path)
            except OSError as e:
                mb_error("Не удалось сохранить", str(e))

    def build_ld(self, path, done):
        sess = self.stats.current[1] if self.stats.current else {}
        sp = self.session

        def work():
            try:
                data = resample_session(sp, 20.0)
                if not data["lapno"]:
                    raise ValueError(tr("Нет целых кругов с телеметрией."))
                write_ld(path, data, 20.0, driver=sess.get("driver", ""), vehicle=sess.get("car", ""), venue=sess.get("track", ""),
                         comment=session_title(sess) if sess else "")
                err = ""
            except Exception as ex:
                err = str(ex)
            try:
                self.after(0, lambda: done(err))
            except (RuntimeError, tk.TclError):
                pass
        self.msg.config(text=tr("Собираю файл .ld…"))
        threading.Thread(target=work, daemon=True).start()

    def export_ld(self):
        if not self.session or not self.items:
            mb_info("Экспорт", "В этой сессии нет телеметрии.")
            return
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".ld", initialdir=tel_folder(self.session),
                                            initialfile=os.path.splitext(os.path.basename(self.session))[0] + ".ld", filetypes=[("MoTeC", "*.ld")])
        if not path:
            return

        def done(err):
            self.msg.config(text=(tr("Сохранено: ") + path) if not err else tr("Ошибка: ") + err)
        self.build_ld(path, done)

    def open_i2(self):
        exe = str(self.app.cfg.get("i2_path", "")).strip()
        if not exe or not os.path.isfile(exe):
            mb_info("MoTeC i2 Pro", "Укажите путь к i2.exe в «Настройки лаунчера» → «Телеметрия».")
            return
        if not self.session or not self.items:
            mb_info("MoTeC i2 Pro", "В этой сессии нет телеметрии.")
            return
        path = os.path.join(tel_folder(self.session), os.path.splitext(os.path.basename(self.session))[0] + ".ld")

        def done(err):
            if err:
                self.msg.config(text=tr("Ошибка: ") + err)
                return
            try:
                subprocess.Popen([exe, path], creationflags=NO_WINDOW)
                self.msg.config(text=tr("Открыто в i2 Pro: ") + path)
            except OSError as e:
                mb_error("Не удалось запустить", str(e))
        self.build_ld(path, done)


class StatsPage(ttk.Frame):
    """Вкладка «Статистика»: сохранённые сессии, круги, сектора, топливо, шины, скорость."""

    def __init__(self, parent, app):
        super().__init__(parent, padding=12)
        self.app = app
        self.sessions, self.current, self._mtime = [], None, 0.0
        self.subject, self.shown, self._rrows = None, None, []
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)
        left = ttk.Frame(self)
        left.grid(row=0, column=0, sticky="ns", padx=(0, 14))
        ttk.Label(left, text="Сессии", style="Head.TLabel").pack(anchor="w")
        self.lst = tk.Listbox(left, width=40, height=24, bg=UI_FIELD, fg=UI_FG, selectbackground=UI_ACCENT,
                              selectforeground=UI_ACCENT_FG, highlightthickness=1, highlightbackground=UI_BORDER,
                              relief="flat", activestyle="none", exportselection=False)
        self.lst.pack(fill="both", expand=True, pady=(4, 6))
        self.lst.bind("<<ListboxSelect>>", lambda e: self.on_select())
        ttk.Button(left, text="Обновить", command=self.reload).pack(fill="x", pady=2)
        ttk.Button(left, text="Экспорт в CSV…", command=self.export).pack(fill="x", pady=2)
        ttk.Button(left, text="Все сессии в один CSV…", command=self.export_all).pack(fill="x", pady=2)
        ttk.Button(left, text="Удалить сессию", command=self.delete).pack(fill="x", pady=2)
        ttk.Button(left, text="Открыть папку статистики", command=self.open_dir).pack(fill="x", pady=2)

        right = ttk.Frame(self)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(2, weight=1)
        self.title = ttk.Label(right, text="Статистика сессий", style="Title.TLabel")
        self.title.grid(row=0, column=0, sticky="w")
        ibar = ttk.Frame(right)
        ibar.grid(row=1, column=0, sticky="ew", pady=(2, 6))
        self.info = ttk.Label(ibar, text="", style="Dim.TLabel")
        self.info.pack(side="left")
        self.back_btn = ttk.Button(ibar, text=tr("← Мои круги"), command=lambda: self.pick_subject(None))
        self.view_btns = {}
        for k, name in (("tel", "Телеметрия"), ("res", "Результаты"), ("laps", "Круги")):
            self.view_btns[k] = ttk.Button(ibar, text=tr(name), command=lambda k=k: self.set_view(k))
            self.view_btns[k].pack(side="right", padx=(4, 0))
        self.vmode = "laps"
        wrap = ttk.Frame(right)
        wrap.grid(row=2, column=0, sticky="nsew")
        wrap.columnconfigure(0, weight=1)
        wrap.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(wrap, columns=[k for k, _, _, _ in STAT_COLS], show="headings", height=8)
        for k, name, w, anchor in STAT_COLS:
            self.tree.heading(k, text=tr(name))
            self.tree.column(k, width=w, minwidth=w, anchor=anchor, stretch=False)
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb = self.sb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        sb.grid(row=0, column=1, sticky="ns")
        sbx = ttk.Scrollbar(wrap, orient="horizontal", command=self.tree.xview)
        sbx.grid(row=1, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=sb.set, xscrollcommand=sbx.set)
        self.rtree = ttk.Treeview(wrap, columns=[k for k, _, _, _ in self.RES_COLS], show="headings", height=8)
        for k, name, w, anchor in self.RES_COLS:
            self.rtree.heading(k, text=tr(name))
            self.rtree.column(k, width=w, minwidth=w, anchor=anchor, stretch=(k == "driver"))
        self.rtree.bind("<<TreeviewSelect>>", self.on_result_pick)
        self.rtree.tag_configure("me", foreground=UI_ACCENT)
        self.rtree.tag_configure("gone", foreground=UI_DIM)
        self.rtree.tag_configure("bestlap", foreground="#b04dff")
        self.rtree.configure(yscrollcommand=sb.set)
        self.tree.tag_configure("best", foreground="#b04dff")
        self.tree.tag_configure("pit", foreground=UI_DIM)
        self.tree.tag_configure("sum", foreground=UI_ACCENT)
        self.wrap = wrap
        self.tel = TelemetryView(right, self)
        self.cards = ttk.Frame(right)
        self.cards.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        tabs = ttk.Frame(right)
        tabs.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        self.ctab, self.tab_btns, self._plot = "time", {}, None
        for k, name in self.CHART_TABS:
            self.tab_btns[k] = ttk.Button(tabs, text=name, command=lambda k=k: self.set_tab(k))
            self.tab_btns[k].pack(side="left", padx=(0, 4))
        self.tabs = tabs
        self.chart = tk.Canvas(right, height=230, bg=UI_FIELD, highlightthickness=1, highlightbackground=UI_BORDER)
        self.chart.grid(row=5, column=0, sticky="ew", pady=(4, 0))
        self.chart.bind("<Configure>", lambda e: self.draw_chart())
        self.chart.bind("<Motion>", self.chart_hover)
        self.chart.bind("<Leave>", lambda e: self.chart.delete("hover"))
        self.msg = ttk.Label(right, text="", style="Dim.TLabel")
        self.msg.grid(row=6, column=0, sticky="w", pady=(4, 0))
        self.reload()

    # ---- данные
    def reload(self, keep=True):
        sel = self.current[0] if (keep and self.current) else None
        self.sessions = load_sessions()
        self.lst.delete(0, "end")
        for path, d in self.sessions:
            n = len(d.get("laps", []))
            self.lst.insert("end", f"{d.get('started', '')[5:16]}  {session_title(d)}  ({n})")
        if not self.sessions:
            self.current = None
            self.show(None)
            return
        idx = next((i for i, (p, _) in enumerate(self.sessions) if p == sel), 0)
        self.lst.selection_clear(0, "end")
        self.lst.selection_set(idx)
        self.current = self.sessions[idx]
        self.show(self.current[1])

    RES_COLS = [("pos", "Поз", 44, "center"), ("cpos", "В классе", 70, "center"), ("number", "№", 50, "center"),
                ("driver", "Пилот", 200, "w"), ("cls", "Класс", 90, "w"), ("car", "Машина", 200, "w"),
                ("laps", "Круги", 56, "center"), ("best", "Лучший", 90, "e"), ("last", "Последний", 90, "e"),
                ("state", "Статус", 80, "w")]

    def set_view(self, k):
        self.vmode = k
        self.apply_view()

    def apply_view(self):
        res = self.vmode == "res"
        tel = self.vmode == "tel"
        if tel:
            for w in (self.wrap, self.cards, self.tabs, self.chart):
                w.grid_remove()
            self.tel.grid(row=2, column=0, rowspan=4, sticky="nsew")
            if self.current:
                self.tel.set_session(self.current[0], self.current[1])
            else:
                self.tel.set_session(None, None)
        else:
            self.tel.grid_remove()
            for w in (self.wrap, self.cards, self.tabs, self.chart):
                w.grid()
        for k, b in self.view_btns.items():
            b.configure(style="Accent.TButton" if k == self.vmode else "TButton")
        if tel:
            return
        if res:
            self.tree.grid_remove()
            self.rtree.grid(row=0, column=0, sticky="nsew")
            self.sb.configure(command=self.rtree.yview)
        else:
            self.rtree.grid_remove()
            self.tree.grid(row=0, column=0, sticky="nsew")
            self.sb.configure(command=self.tree.yview)
        for k, b in self.view_btns.items():
            b.configure(style="Accent.TButton" if k == self.vmode else "TButton")

    def fill_results(self, d):
        for i in self.rtree.get_children():
            self.rtree.delete(i)
        rows = [r for r in (d.get("results") or []) if isinstance(r, dict)] if d else []
        self._rrows = rows
        bests = [r["best"] for r in rows if isinstance(r.get("best"), (int, float)) and r["best"] > 0]
        top = min(bests) if bests else None
        for r in rows:
            tags = []
            if r.get("player"):
                tags.append("me")
            elif r.get("present") is False:
                tags.append("gone")
            if top and r.get("best") == top:
                tags.append("bestlap")
            state = tr("в боксах") if r.get("pit") and r.get("present", True) else (tr("уехал") if r.get("present") is False else tr("на трассе"))
            self.rtree.insert("", "end", iid=str(rows.index(r)), tags=tags, values=[
                r.get("pos", ""), r.get("cpos", ""), r.get("number", ""), r.get("driver", ""), r.get("cls", ""),
                r.get("car", ""), r.get("laps", ""), fmt_time(r.get("best")) if r.get("best") else "-",
                fmt_time(r.get("last")) if r.get("last") else "-", state])
        if not rows and d is not None:
            self.rtree.insert("", "end", values=["", "", "", tr("Результаты появятся, когда оверлей запишет эту сессию"), "", "", "", "", "", ""])

    def on_result_pick(self, e=None):
        sel = self.rtree.selection()
        if not sel or not sel[0].isdigit() or int(sel[0]) >= len(self._rrows) or not self.current:
            return
        r = self._rrows[int(sel[0])]
        d = self.current[1]
        if r.get("player") or not r.get("key"):
            self.pick_subject(None)
            return
        if driver_view(d, r["key"]) is None:
            self.msg.config(text=tr("Для этого пилота нет записи кругов: она ведётся в сессиях, записанных версией 2.5.2 и новее."))
            return
        self.pick_subject(r["key"])

    def pick_subject(self, key):
        self.subject = key
        self.vmode = "laps"
        if self.current:
            self.show(self.current[1])

    def set_tab(self, k):
        self.ctab = k
        self.draw_chart()

    def on_select(self):
        sel = self.lst.curselection()
        if sel and sel[0] < len(self.sessions):
            if self.current is None or self.sessions[sel[0]][0] != self.current[0]:
                self.subject = None
            self.current = self.sessions[sel[0]]
            self.show(self.current[1])

    def auto_refresh(self):
        """Дёргается лаунчером: подхватывает новые круги, пока открыта вкладка."""
        try:
            files = [os.path.join(STATS_DIR, n) for n in os.listdir(STATS_DIR) if n.lower().endswith(".json")]
            newest = max((os.path.getmtime(f) for f in files), default=0.0)
        except OSError:
            newest = 0.0
        if newest != self._mtime:
            self._mtime = newest
            self.reload()

    def show(self, d):
        for i in self.tree.get_children():
            self.tree.delete(i)
        for w in self.cards.winfo_children():
            w.destroy()
        self.fill_results(d)
        base = d
        d = driver_view(d, self.subject) if (d is not None and self.subject) else d
        if d is None and base is not None:
            self.subject = None
            d = base
        self.shown = d
        if self.subject:
            self.back_btn.pack(side="right", padx=(4, 0))
        else:
            self.back_btn.pack_forget()
        self.apply_view()
        if d is None:
            self.title.config(text=tr("Статистика сессий"))
            self.info.config(text=tr("Пока пусто. Статистика пишется, пока работает оверлей: каждый круг сохраняется в папку stats."))
            self.draw_chart()
            return
        self.title.config(text=session_title(d) + (f" — {d.get('driver', '')}" if self.subject else ""))
        self.info.config(text=f"{d.get('car', '')}   {d.get('cls', '')}   {tr('начало')}: {d.get('started', '')}")
        sm = summarize(d)
        laps = [x for x in d["laps"] if isinstance(x, dict)]
        for x in laps:
            tags = []
            if x.get("time") and x["time"] == sm["best"]:
                tags.append("best")
            if x.get("pit"):
                tags.append("pit")
            self.tree.insert("", "end", values=lap_row(x), tags=tags)
        if laps:
            self.tree.see(self.tree.get_children()[-1])
        bs = sm["best_s"]
        self.tree.insert("", "end", values=["", tr("лучшее"), fmt_time(bs[0], True), fmt_time(bs[1], True), fmt_time(bs[2], True)] + [""] * (len(STAT_COLS) - 5), tags=["sum"])
        cards = [("Круги", str(sm["laps"])), ("Лучший круг", fmt_time(sm["best"])), ("Средний (без пит)", fmt_time(sm["avg"])),
                 ("Топливо, л/круг", fmt_num(sm["fuel"], 2)), ("Износ шин, %/круг", fmt_num(sm["wear"], 2)),
                 ("Макс. скорость", fmt_num(sm["vmax"], 0, " км/ч")), ("Мин. скорость", fmt_num(sm["vmin"], 0, " км/ч"))]
        for i, (name, val) in enumerate(cards):
            box = tk.Frame(self.cards, bg=UI_PANEL, padx=10, pady=5, highlightthickness=1, highlightbackground=UI_BORDER)
            box.grid(row=0, column=i, padx=(0, 6), sticky="w")
            tk.Label(box, text=tr(name), bg=UI_PANEL, fg=UI_DIM, font=("Segoe UI", 8)).pack(anchor="w")
            tk.Label(box, text=tr(val) if val.endswith("км/ч") else val, bg=UI_PANEL, fg=UI_FG, font=("Segoe UI Semibold", 12)).pack(anchor="w")
        self.draw_chart()

    CHART_TABS = [("time", "Время круга"), ("sect", "Сектора"), ("fuel", "Топливо"), ("tyres", "Остаток шин"),
                  ("wear", "Износ за круг"), ("speed", "Скорость")]

    def chart_series(self, d, tab):
        """Ряды для графика: [(имя, цвет, [(круг, значение|None)])], форматтер значений, подпись оси."""
        laps = [x for x in (d["laps"] if d else []) if isinstance(x, dict)]
        num = lambda v: v if isinstance(v, (int, float)) and v > 0 else None
        if tab == "time":
            return [("время круга", UI_ACCENT, [(x.get("lap"), None if x.get("pit") else num(x.get("time"))) for x in laps])], fmt_time
        if tab == "sect":
            cols = {"s1": "#ff5a4f", "s2": "#4da3ff", "s3": "#ffc107"}
            out = []
            for k in ("s1", "s2", "s3"):
                vals = [None if x.get("pit") else num(x.get(k)) for x in laps]
                best = min((v for v in vals if v), default=None)
                out.append((k.upper() + " к лучшему", cols[k], [(x.get("lap"), (v - best) if (v and best) else None) for x, v in zip(laps, vals)]))
            return out, lambda v: f"+{v:.2f}"
        if tab == "fuel":
            return [("расход за круг, л", "#2ee26d", [(x.get("lap"), None if x.get("pit") else num(x.get("fuel_used"))) for x in laps])], lambda v: f"{v:.2f}"
        if tab == "tyres":
            cols = ["#ff5a4f", "#4da3ff", "#ffc107", "#2ee26d"]
            out = []
            for i, n in enumerate(("FL", "FR", "RL", "RR")):
                out.append((n, cols[i], [(x.get("lap"), (x["tyres"][i] * 100) if isinstance(x.get("tyres"), list) and len(x["tyres"]) > i and isinstance(x["tyres"][i], (int, float)) else None) for x in laps]))
            return out, lambda v: f"{v:.0f}%"
        if tab == "wear":
            cols = ["#ff5a4f", "#4da3ff", "#ffc107", "#2ee26d"]
            out = []
            for i, n in enumerate(("FL", "FR", "RL", "RR")):
                out.append((n, cols[i], [(x.get("lap"), (x["tyre_used"][i] * 100) if isinstance(x.get("tyre_used"), list) and len(x["tyre_used"]) > i
                                          and isinstance(x["tyre_used"][i], (int, float)) and not x.get("pit") else None) for x in laps]))
            return out, lambda v: f"{v:.2f}%"
        return [("Vmax", "#ff5a4f", [(x.get("lap"), num(x.get("vmax"))) for x in laps]),
                ("Vmin", "#4da3ff", [(x.get("lap"), num(x.get("vmin"))) for x in laps])], lambda v: f"{v:.0f}"

    def draw_chart(self):
        c = self.chart
        c.delete("all")
        for i, (k, name) in enumerate(self.CHART_TABS):
            self.tab_btns[k].configure(style="Accent.TButton" if k == self.ctab else "TButton")
        d = self.shown
        W, H = max(300, c.winfo_width()), max(120, c.winfo_height())
        series, fmt = self.chart_series(d, self.ctab)
        laps = [x.get("lap") for x in (d["laps"] if d else []) if isinstance(x, dict)]
        vals = [v for _, _, pts in series for _, v in pts if v is not None]
        self._plot = None
        if len(laps) < 2 or len(vals) < 2:
            c.create_text(W / 2, H / 2, text=tr("График появится после двух кругов"), fill=UI_DIM)
            return
        lo, hi = min(vals), max(vals)
        if self.ctab == "sect":
            lo = 0.0
        pad = max((hi - lo) * 0.1, 0.05 if self.ctab in ("time", "sect", "fuel", "wear") else 1.0)
        lo, hi = (lo if self.ctab == "sect" else lo - pad), hi + pad
        if self.ctab == "tyres":
            hi = min(hi, 100.0)
        L, R, T, B = 58, 14, 24, 20
        px = lambda i: L + (W - L - R) * i / max(1, len(laps) - 1)
        py = lambda v: T + (H - T - B) * (1 - (v - lo) / (hi - lo))
        for g in range(5):                                           # сетка и подписи оси
            v = lo + (hi - lo) * g / 4
            y = py(v)
            c.create_line(L, y, W - R, y, fill=UI_BORDER)
            c.create_text(L - 6, y, text=fmt(v), fill=UI_DIM, anchor="e", font=("Segoe UI", 8))
        step = max(1, len(laps) // 12)
        for i, lap in enumerate(laps):
            if i % step == 0:
                c.create_text(px(i), H - 8, text=str(lap), fill=UI_DIM, font=("Segoe UI", 8))
        for i, x in enumerate([x for x in d["laps"] if isinstance(x, dict)]):   # пит-круги — пунктирные линии
            if x.get("pit"):
                c.create_line(px(i), T, px(i), H - B, fill=UI_DIM, dash=(3, 3))
                c.create_text(px(i), T - 8, text="PIT", fill=UI_DIM, font=("Segoe UI", 7))
        lx = L
        for name, col, pts in series:
            seg = []
            for i, (lap, v) in enumerate(pts):
                if v is None:
                    if len(seg) >= 4:
                        c.create_line(*seg, fill=col, width=2)
                    seg = []
                    continue
                seg += [px(i), py(v)]
                c.create_oval(px(i) - 3, py(v) - 3, px(i) + 3, py(v) + 3, fill=col, outline="")
            if len(seg) >= 4:
                c.create_line(*seg, fill=col, width=2)
            c.create_rectangle(lx, 6, lx + 10, 14, fill=col, outline="")
            c.create_text(lx + 14, 10, text=tr(name), fill=UI_FG, anchor="w", font=("Segoe UI", 8))
            lx += 40 + 7 * len(tr(name))
        if self.ctab == "time":                                       # линия лучшего круга
            best = min((v for _, v in series[0][2] if v), default=None)
            if best:
                c.create_line(L, py(best), W - R, py(best), fill="#b04dff", dash=(5, 3))
                c.create_text(W - R - 2, py(best) - 7, text=tr("лучший ") + fmt_time(best), fill="#b04dff", anchor="e", font=("Segoe UI", 8))
        self._plot = (series, fmt, laps, px, L, R, T, H - B)

    def chart_hover(self, e):
        c = self.chart
        c.delete("hover")
        if not self._plot:
            return
        series, fmt, laps, px, L, R, T, yb = self._plot
        if not (L <= e.x <= c.winfo_width() - R):
            return
        i = min(range(len(laps)), key=lambda k: abs(px(k) - e.x))
        x = px(i)
        c.create_line(x, T, x, yb, fill=UI_FG, tags="hover")
        lines = [f"{tr('Круг')} {laps[i]}"] + [f"{tr(n)}: {fmt(pts[i][1])}" for n, _, pts in series if pts[i][1] is not None]
        w = 14 + 7 * max(len(t) for t in lines)
        bx = x + 10 if x + 10 + w < c.winfo_width() else x - 10 - w
        c.create_rectangle(bx, T + 4, bx + w, T + 8 + 14 * len(lines), fill=UI_PANEL, outline=UI_BORDER, tags="hover")
        for k, t in enumerate(lines):
            c.create_text(bx + 6, T + 12 + 14 * k, text=t, fill=UI_FG, anchor="w", font=("Segoe UI", 8), tags="hover")

    # ---- действия
    def export(self):
        if not self.current:
            return
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".csv", initialdir=STATS_DIR,
                                            initialfile=os.path.splitext(os.path.basename(self.current[0]))[0] + ".csv",
                                            filetypes=[("CSV", "*.csv")])
        if path:
            try:
                export_csv(path, self.shown or self.current[1])
                self.msg.config(text=tr("Сохранено: ") + path)
            except OSError as e:
                mb_error("Не удалось сохранить", str(e))

    def export_all(self):
        if not self.sessions:
            mb_info("Экспорт", "Пока нет сохранённых сессий.")
            return
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".csv", initialdir=STATS_DIR, initialfile="yassno-all-sessions.csv",
                                            filetypes=[("CSV", "*.csv")])
        if path:
            try:
                export_all_csv(path, self.sessions)
                self.msg.config(text=tr("Сохранено: ") + path + "  —  " + tr("в Google Таблицах: Файл → Импортировать → Загрузка"))
            except OSError as e:
                mb_error("Не удалось сохранить", str(e))

    def delete(self):
        if not self.current:
            return
        if not mb_yesno("Удалить сессию", "Удалить выбранную сессию из статистики? Это нельзя отменить."):
            return
        try:
            os.remove(self.current[0])
        except OSError as e:
            mb_error("Не удалось удалить", str(e))
        self.current = None
        self.reload(keep=False)

    def open_dir(self):
        try:
            os.makedirs(STATS_DIR, exist_ok=True)
            if hasattr(os, "startfile"):
                os.startfile(STATS_DIR)
        except OSError as e:
            mb_error("Не удалось открыть", str(e))


# ---------------------------------------------------------------------------
# главное окно
# ---------------------------------------------------------------------------
class SettingsPage(ttk.Frame):
    """Настройки самого лаунчера: тема, язык, запуск с Windows, трей и т.п."""
    THEMES = [("dark", "Тёмная"), ("light", "Светлая"), ("system", "Как в Windows")]
    LANGS = [("ru", "Русский"), ("en", "English")]

    def __init__(self, parent, app):
        super().__init__(parent, padding=12)
        self.app = app
        cfg = app.cfg
        self.v = {
            "win_start": tk.BooleanVar(value=bool(cfg["win_start"])),
            "start_min": tk.BooleanVar(value=bool(cfg["start_min"])),
            "min_tray": tk.BooleanVar(value=bool(cfg["min_tray"])),
            "close_tray": tk.BooleanVar(value=bool(cfg["close_tray"])),
            "autostart": app.auto_var, "close_with_game": app.close_var, "launch_with_game": app.launch_var,
            "stop_on_exit": tk.BooleanVar(value=bool(cfg["stop_on_exit"])),
            "poll_sec": tk.DoubleVar(value=float(cfg["poll_sec"])),
            "telemetry": tk.BooleanVar(value=bool(cfg.get("telemetry", True))),
            "auto_update": tk.BooleanVar(value=bool(cfg.get("auto_update", True))),
        }
        self.i2_var = tk.StringVar(value=str(cfg.get("i2_path", "")))
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        sc = tk.Canvas(self, bg=UI_BG, highlightthickness=0, borderwidth=0)
        sbar = ttk.Scrollbar(self, orient="vertical", command=sc.yview)
        sc.configure(yscrollcommand=sbar.set)
        sc.grid(row=0, column=0, sticky="nsew")
        sbar.grid(row=0, column=1, sticky="ns")
        body = ttk.Frame(sc)
        win = sc.create_window(0, 0, window=body, anchor="nw")
        sc.bind("<Configure>", lambda e: sc.itemconfigure(win, width=e.width))
        body.bind("<Configure>", lambda e: sc.configure(scrollregion=sc.bbox("all")))
        self.sc = sc
        self.ver_foot = ttk.Label(self, text="", style="Dim.TLabel")
        self.ver_foot.grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))

        enable_wheel(sc, self)
        self.theme_var = tk.StringVar(value=tr(dict(self.THEMES)[cfg["theme"]]))
        self.lang_var = tk.StringVar(value=dict(self.LANGS)[cfg["lang"]])
        ttk.Label(body, text="Настройки лаунчера", style="Title.TLabel").grid(row=0, column=0, sticky="w", columnspan=3)
        ttk.Label(body, text="Всё, что относится к самой программе. Настройки оверлея — на страницах игр.",
                  style="Dim.TLabel").grid(row=1, column=0, sticky="w", columnspan=3, pady=(2, 10))

        col1, col2, col3 = ttk.Frame(body), ttk.Frame(body), ttk.Frame(body)
        col1.grid(row=2, column=0, sticky="nw", padx=(0, 24))
        col2.grid(row=2, column=1, sticky="nw", padx=(0, 24))
        col3.grid(row=2, column=2, sticky="nw")

        box = ttk.LabelFrame(col1, text="Внешний вид", padding=10)
        box.pack(fill="x", pady=(0, 10))
        ttk.Label(box, text="Тема").grid(row=0, column=0, sticky="w", padx=(0, 12), pady=4)
        cb = ttk.Combobox(box, textvariable=self.theme_var, values=[tr(n) for _, n in self.THEMES], state="readonly", width=16)
        cb.grid(row=0, column=1, sticky="w")
        cb.bind("<<ComboboxSelected>>", self.on_theme)
        ttk.Label(box, text="Язык").grid(row=1, column=0, sticky="w", padx=(0, 12), pady=4)
        cb2 = ttk.Combobox(box, textvariable=self.lang_var, values=[n for _, n in self.LANGS], state="readonly", width=16)
        cb2.grid(row=1, column=1, sticky="w")
        cb2.bind("<<ComboboxSelected>>", self.on_lang)

        box = ttk.LabelFrame(col1, text="Запуск", padding=10)
        box.pack(fill="x", pady=(0, 10))
        self.tg(box, 0, "Запускать лаунчер вместе с Windows", "win_start")
        self.tg(box, 1, "Запускать свёрнутым", "start_min")

        box = ttk.LabelFrame(col1, text="Свёрнутое окно", padding=10)
        box.pack(fill="x", pady=(0, 10))
        self.tg(box, 0, "Сворачивать в трей (значок у часов)", "min_tray")
        self.tg(box, 1, "Крестик окна сворачивает в трей, а не закрывает", "close_tray")
        self.tray_lbl = ttk.Label(box, text="", style="Dim.TLabel", wraplength=380, justify="left")
        self.tray_lbl.grid(row=2, column=0, sticky="w", pady=(4, 0))
        self.tray_btn = ttk.Button(box, text="Установить поддержку трея", command=self.install_tray)
        self.tray_btn.grid(row=3, column=0, sticky="w", pady=(6, 0))

        box = ttk.LabelFrame(col3, text="Горячие клавиши оверлея", padding=10)
        box.pack(fill="x", pady=(0, 10))
        hk = read_json(profile_path("lmu")).get("hotkeys") if isinstance(read_json(profile_path("lmu")), dict) else None
        hk = hk if isinstance(hk, dict) else {}
        self.hk_vars = {}
        for i, (name, label) in enumerate(HOTKEYS):
            cur = str(hk.get(name) or HOTKEY_DEFAULTS[name])
            parts = cur.split("+")
            mod, key = ("+".join(parts[:-1]), parts[-1]) if len(parts) > 1 else ("Ctrl+Shift", parts[0])
            if mod not in HK_MODS:
                mod = "Ctrl+Shift"
            mv, kv = tk.StringVar(value=mod), tk.StringVar(value=key.upper())
            self.hk_vars[name] = (mv, kv)
            ttk.Label(box, text=label).grid(row=i, column=0, sticky="w", padx=(0, 10), pady=3)
            c1 = ttk.Combobox(box, textvariable=mv, values=HK_MODS, state="readonly", width=11)
            c1.grid(row=i, column=1, padx=(0, 4))
            c2 = ttk.Combobox(box, textvariable=kv, values=HK_KEYS, state="readonly", width=5)
            c2.grid(row=i, column=2)
            c1.bind("<<ComboboxSelected>>", lambda e: self.save_hotkeys())
            c2.bind("<<ComboboxSelected>>", lambda e: self.save_hotkeys())
        ttk.Button(box, text="Стандартные", command=self.reset_hotkeys).grid(row=len(HOTKEYS), column=0, sticky="w", pady=(6, 0))
        self.hk_msg = ttk.Label(box, text="Применяются при следующем запуске оверлея.", style="Dim.TLabel")
        self.hk_msg.grid(row=len(HOTKEYS) + 1, column=0, columnspan=3, sticky="w", pady=(4, 0))

        box = ttk.LabelFrame(col2, text="Оверлей и игра", padding=10)
        box.pack(fill="x", pady=(0, 10))
        self.tg(box, 0, "Запускать оверлей вместе с игрой (кнопка «Запустить игру»)", "launch_with_game")
        self.tg(box, 1, "Сам определять игру и запускать оверлей", "autostart")
        self.tg(box, 2, "Закрывать оверлей вместе с игрой", "close_with_game")
        self.tg(box, 3, "Останавливать оверлей при выходе из лаунчера", "stop_on_exit")
        r = ttk.Frame(box)
        r.grid(row=4, column=0, sticky="w", pady=(6, 0))
        ttk.Label(r, text="Проверять запущенную игру каждые").pack(side="left")
        ttk.Spinbox(r, from_=1, to=10, increment=1, textvariable=self.v["poll_sec"], width=4, command=self.on_poll).pack(side="left", padx=6)
        ttk.Label(r, text="сек.").pack(side="left")
        ttk.Button(box, text="Процессы игр…", command=app.edit_procs).grid(row=5, column=0, sticky="w", pady=(8, 0))

        box = ttk.LabelFrame(col2, text="Запуск игр", padding=10)
        box.pack(fill="x", pady=(0, 10))
        for i, (g, name) in enumerate(GAMES.items()):
            ttk.Label(box, text=name).grid(row=i, column=0, sticky="w", padx=(0, 8), pady=3)
            ttk.Entry(box, textvariable=app.game_vars[g], width=34).grid(row=i, column=1, pady=3)
            ttk.Button(box, text="Обзор…", command=lambda g=g: app.browse_game(g)).grid(row=i, column=2, padx=(6, 0))
        ttk.Label(box, text="Пусто — игра запускается через Steam.", style="Dim.TLabel").grid(row=len(GAMES), column=0, columnspan=3, sticky="w", pady=(4, 0))

        box = ttk.LabelFrame(col2, text="Файлы", padding=10)
        box.pack(fill="x", pady=(0, 10))
        for i, (name, path) in enumerate((("Открыть папку профилей", PROFILES), ("Открыть папку журналов", LOGS),
                                          ("Открыть папку карт трасс", MAP_DIR), ("Открыть папку лаунчера", HERE))):
            ttk.Button(box, text=name, command=lambda p=path: self.open_dir(p)).grid(row=i // 2, column=i % 2, sticky="w", padx=(0, 8), pady=3)

        box = ttk.LabelFrame(col1, text="Обновление программы", padding=10)
        box.pack(fill="x", pady=(0, 10))
        self.ver_lbl = ttk.Label(box, text="", style="Dim.TLabel", justify="left")
        self.ver_lbl.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 4))
        ttk.Button(box, text="Установить из zip…", command=self.install_update).grid(row=1, column=0, sticky="w", padx=(0, 8))
        ttk.Button(box, text="Откатить", command=self.rollback).grid(row=1, column=1, sticky="w")
        ttk.Button(box, text="Проверить обновления", command=lambda: self.app.check_update(True)).grid(row=2, column=0, sticky="w", padx=(0, 8), pady=(8, 0))
        Toggle(box, "Проверять при запуске лаунчера", self.v["auto_update"]).grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.refresh_version()

        box = ttk.LabelFrame(col2, text="Перенос настроек", padding=10)
        box.pack(fill="x", pady=(0, 10))
        ttk.Button(box, text="Экспорт настроек…", command=self.export_all).grid(row=0, column=0, sticky="w", padx=(0, 8), pady=3)
        ttk.Button(box, text="Импорт настроек…", command=self.import_all).grid(row=0, column=1, sticky="w", pady=3)
        ttk.Label(box, text="Один файл: профили LMU и rFactor 2 (раскладка окон, виды, цвета)\nи ваши программы. Удобно для копии или другого ПК.",
                  style="Dim.TLabel", justify="left").grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 0))

        box = ttk.LabelFrame(col3, text="Диагностика", padding=10)
        box.pack(fill="x", pady=(0, 10))
        ttk.Button(box, text="Проверить систему…", command=self.diagnose).grid(row=0, column=0, sticky="w", pady=3)
        ttk.Label(box, text="Покажет, запущена ли игра, виден ли оверлею\nеё поток данных и что писал оверлей в журнал.",
                  style="Dim.TLabel", justify="left").grid(row=1, column=0, sticky="w", pady=(2, 0))

        box = ttk.LabelFrame(col3, text="Телеметрия", padding=10)
        box.pack(fill="x", pady=(0, 10))
        self.tg(box, 0, "Записывать телеметрию (скорость, педали, шины…)", "telemetry")
        ttk.Label(box, text="20 записей в секунду, около 50 КБ на круг.\nЕсли оверлей нагружает компьютер — выключите.\nПрименяется сразу, перезапуск не нужен.",
                  style="Dim.TLabel", justify="left").grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 6))
        ttk.Label(box, text="MoTeC i2 Pro (i2.exe)").grid(row=2, column=0, sticky="w")
        r2 = ttk.Frame(box)
        r2.grid(row=3, column=0, sticky="w", pady=(2, 0))
        ttk.Entry(r2, textvariable=self.i2_var, width=30).pack(side="left")
        ttk.Button(r2, text="Обзор…", command=self.browse_i2).pack(side="left", padx=(6, 0))
        self.i2_var.trace_add("write", lambda *a: (self.app.cfg.__setitem__("i2_path", self.i2_var.get().strip()), self.app.save_cfg()))

        box = ttk.LabelFrame(col3, text="Сброс", padding=10)
        box.pack(fill="x")
        ttk.Button(box, text="Сбросить настройки лаунчера", command=self.reset).pack(anchor="w")
        ttk.Label(box, text="Профили оверлея (LMU и rFactor 2) не затрагиваются.", style="Dim.TLabel").pack(anchor="w", pady=(4, 0))

        for key in ("win_start", "start_min", "min_tray", "close_tray", "stop_on_exit", "telemetry", "auto_update"):
            self.v[key].trace_add("write", lambda *a: self.apply())
        self.refresh_tray()

    def tg(self, parent, r, text, key):
        Toggle(parent, text, self.v[key]).grid(row=r, column=0, sticky="w", pady=4)

    def refresh_tray(self):
        ok = tray_supported()
        if ok:
            self.tray_lbl.config(text=tr("Поддержка трея установлена."))
            self.tray_btn.grid_remove()
        else:
            self.tray_lbl.config(text=tr("Для трея нужны пакеты pystray и pillow. Без них окно просто сворачивается на панель задач."))
            self.tray_btn.grid()

    def install_tray(self):
        self.tray_lbl.config(text=tr("Устанавливаю…"))
        self.update_idletasks()

        def work():
            try:
                subprocess.run([sys.executable, "-m", "pip", "install", "pystray", "pillow"], capture_output=True,
                               timeout=240, creationflags=NO_WINDOW)
            except Exception:
                pass
            try:
                self.after(0, self.refresh_tray)
            except (RuntimeError, tk.TclError):
                pass
        threading.Thread(target=work, daemon=True).start()

    def open_dir(self, path):
        try:
            os.makedirs(path, exist_ok=True)
            if hasattr(os, "startfile"):
                os.startfile(path)
        except OSError as e:
            mb_error("Не удалось открыть", str(e))

    def on_poll(self):
        self.apply()

    def apply(self):
        cfg = self.app.cfg
        for k in ("win_start", "start_min", "min_tray", "close_tray", "stop_on_exit", "telemetry", "auto_update"):
            cfg[k] = bool(self.v[k].get())
        cfg["i2_path"] = self.i2_var.get().strip()
        try:
            cfg["poll_sec"] = max(1.0, min(10.0, float(self.v["poll_sec"].get())))
        except (tk.TclError, ValueError):
            pass
        set_win_start(cfg["win_start"], cfg["start_min"])
        self.app.save_cfg()

    def browse_i2(self):
        path = filedialog.askopenfilename(parent=self, title=tr("Выберите i2.exe"), filetypes=[(tr("Программы"), "*.exe"), (tr("Все файлы"), "*.*")])
        if path:
            self.i2_var.set(path.replace("/", "\\") if os.name == "nt" else path)

    def on_theme(self, e=None):
        name = self.theme_var.get()
        for code, n in self.THEMES:
            if tr(n) == name:
                self.app.cfg["theme"] = code
        self.app.save_cfg()
        self.app.root.after(30, self.app.rebuild)

    def on_lang(self, e=None):
        name = self.lang_var.get()
        for code, n in self.LANGS:
            if n == name:
                self.app.cfg["lang"] = code
        self.app.save_cfg()
        self.app.root.after(30, self.app.rebuild)

    @staticmethod
    def overlay_version():
        try:
            with open(OVERLAY, encoding="utf-8") as f:
                head = f.read(12000)
            m = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', head)
            return m.group(1) if m else "?"
        except OSError:
            return "—"

    def refresh_version(self):
        self.ver_foot.config(text=f"Yassno Overlay  ·  {APP_VERSION}")
        def when(f):
            try:
                return time.strftime("%d.%m.%Y %H:%M", time.localtime(os.path.getmtime(os.path.join(HERE, f))))
            except OSError:
                return "—"
        self.ver_lbl.config(text=f"{tr('Оверлей')}: {when('yassno_overlay.py')}\n{tr('Лаунчер')}: {when('yassno_launcher.py')}")

    def install_update(self):
        path = filedialog.askopenfilename(title=tr("Архив с обновлением"), filetypes=[("ZIP", "*.zip")])
        if not path:
            return
        try:
            zf = zipfile.ZipFile(path)
            found = {}
            for n in zf.namelist():
                base = n.replace("\\", "/").split("/")
                if base[-1] in UPDATE_FILES and ".." not in base:
                    found.setdefault(base[-1], n)
            if "yassno_overlay.py" not in found or "yassno_launcher.py" not in found:
                mb_error("Не подходит", "В архиве нет yassno_overlay.py и yassno_launcher.py.")
                return
            for name in ("yassno_overlay.py", "yassno_launcher.py"):          # не ставим то, что не компилируется
                compile(zf.read(found[name]), name, "exec")
        except Exception as e:
            mb_error("Не удалось прочитать архив", str(e))
            return
        if not mb_yesno("Обновление", "Установить обновление? Текущие файлы сохранятся в папке backup. Настройки, карты и статистика не меняются."):
            return
        stamp = time.strftime("%Y%m%d-%H%M%S")
        bdir = os.path.join(BACKUP_DIR, stamp)
        try:
            os.makedirs(bdir, exist_ok=True)
            for name in found:
                dst = os.path.join(HERE, name)
                if os.path.isfile(dst):
                    shutil.copyfile(dst, os.path.join(bdir, name))
                with open(dst + ".new", "wb") as f:
                    f.write(zf.read(found[name]))
                os.replace(dst + ".new", dst)
        except OSError as e:
            mb_error("Не удалось сохранить", str(e))
            return
        self.refresh_version()
        mb_info("Готово", "Обновление установлено. Закройте и снова откройте лаунчер, затем перезапустите оверлей.")

    def rollback(self):
        try:
            last = sorted(d for d in os.listdir(BACKUP_DIR) if os.path.isdir(os.path.join(BACKUP_DIR, d)))[-1]
        except (OSError, IndexError):
            mb_info("Откат", "Резервных копий пока нет.")
            return
        if not mb_yesno("Откат", f"Вернуть файлы программы из копии {last}?"):
            return
        bdir = os.path.join(BACKUP_DIR, last)
        try:
            for name in os.listdir(bdir):
                shutil.copyfile(os.path.join(bdir, name), os.path.join(HERE, name))
            shutil.rmtree(bdir)
        except OSError as e:
            mb_error("Не удалось сохранить", str(e))
            return
        self.refresh_version()
        mb_info("Готово", "Файлы возвращены. Закройте и снова откройте лаунчер.")

    def save_hotkeys(self):
        combo = {n: f"{m.get()}+{k.get()}" for n, (m, k) in self.hk_vars.items()}
        seen = {}
        for n, c in combo.items():
            if c in seen:
                self.hk_msg.config(text=tr("Одинаковое сочетание у двух действий — не сохранено."), foreground="#ff5a4f")
                return
            seen[c] = n
        for g in GAMES:
            pp = profile_path(g)
            data = read_json(pp)
            data = data if isinstance(data, dict) else load_settings(g)
            data["hotkeys"] = combo
            try:
                write_json(pp, data)
            except OSError as e:
                mb_error("Не удалось сохранить", str(e))
                return
        self.hk_msg.config(text=tr("Сохранено. Применится при следующем запуске оверлея."), foreground="")

    def reset_hotkeys(self):
        for n, (m, k) in self.hk_vars.items():
            parts = HOTKEY_DEFAULTS[n].split("+")
            m.set("+".join(parts[:-1]))
            k.set(parts[-1])
        self.save_hotkeys()

    def export_all(self):
        path = filedialog.asksaveasfilename(title=tr("Экспорт настроек"), defaultextension=".json", initialfile="yassno-settings.json",
                                            filetypes=[("JSON", "*.json")])
        if not path:
            return
        data = {"format": "yassno-settings", "version": 1, "saved": time.strftime("%Y-%m-%d %H:%M"),
                "profiles": {g: read_json(profile_path(g)) or load_settings(g) for g in GAMES},
                "custom_apps": self.app.cfg.get("custom_apps", [])}
        try:
            write_json(path, data)
        except OSError as e:
            mb_error("Не удалось сохранить", str(e))
            return
        mb_info("Готово", f"Настройки сохранены:\n{path}")

    def import_all(self):
        path = filedialog.askopenfilename(title=tr("Импорт настроек"), filetypes=[("JSON", "*.json")])
        if not path:
            return
        data = read_json(path)
        if not isinstance(data, dict) or data.get("format") != "yassno-settings" or not isinstance(data.get("profiles"), dict):
            mb_error("Не подходит", "Это не файл настроек Yassno Overlay.")
            return
        if not mb_yesno("Импорт", "Заменить текущие профили настроек (LMU и rFactor 2) данными из файла? Старые сохранятся рядом как .bak."):
            return
        n = 0
        for g in GAMES:
            prof = data["profiles"].get(g)
            if not isinstance(prof, dict):
                continue
            pp = profile_path(g)
            try:
                if os.path.isfile(pp):
                    shutil.copyfile(pp, pp + ".bak")
                write_json(pp, prof)
                n += 1
            except OSError as e:
                mb_error("Не удалось сохранить", str(e))
                return
        apps = data.get("custom_apps")
        if isinstance(apps, list):
            self.app.cfg["custom_apps"] = [a for a in apps if isinstance(a, dict)]
            self.app.save_cfg()
        mb_info("Готово", f"Загружено профилей: {n}. Перезапустите оверлей, чтобы применить.")
        self.app.root.after(30, self.app.rebuild)

    def diagnose(self):
        win = tk.Toplevel(self.app.root)
        win.title(tr("Диагностика"))
        win.geometry("760x560")
        txt = tk.Text(win, wrap="word", font=("Consolas", 10), padx=10, pady=8)
        txt.pack(fill="both", expand=True)
        txt.insert("end", tr("Проверяю…") + "\n")
        txt.config(state="disabled")

        def out(line=""):
            def put():
                try:
                    txt.config(state="normal")
                    txt.insert("end", line + "\n")
                    txt.config(state="disabled")
                except tk.TclError:
                    pass
            self.app.root.after(0, put)

        def work():
            out(f"Python {sys.version.split()[0]}   Tk {tk.TkVersion}   {sys.platform}")
            out(f"Оверлей: {'есть, ' + str(os.path.getsize(OVERLAY)) + ' байт' if os.path.isfile(OVERLAY) else 'НЕ НАЙДЕН'}")
            out(f"Логотипов: {len(glob.glob(os.path.join(HERE, 'logos', '*.png')))}    карт трасс: {len(glob.glob(os.path.join(MAP_DIR, '*.json')))}")
            procs = process_names()
            for g, name in GAMES.items():
                names = [x.strip().lower() for x in str(self.app.cfg["procs"].get(g, "")).split(",") if x.strip()]
                out("")
                out(f"=== {name} ===")
                out(f"Игра запущена: {'да' if any(n in procs for n in names) else 'нет'}   (ищу процесс: {', '.join(names) or '—'})")
                out(f"Профиль: {'есть' if os.path.isfile(profile_path(g)) else 'ещё не создан'}")
                if os.path.isfile(OVERLAY) and os.name == "nt":
                    try:
                        r = subprocess.run([sys.executable, OVERLAY, "--game", g, "--diagnose"], capture_output=True, timeout=12,
                                           creationflags=NO_WINDOW, cwd=HERE)
                        out("Что видит оверлей в памяти игры:")
                        out((r.stdout.decode("utf-8", "replace") or "—").strip()[-1500:])
                    except Exception as e:
                        out(f"Проверка памяти не удалась: {e}")
                lp = os.path.join(LOGS, f"overlay-{g}.log")
                try:
                    with open(lp, encoding="utf-8", errors="replace") as f:
                        tail = f.read().strip().splitlines()[-8:]
                    out("Журнал оверлея (последние строки):")
                    for ln in tail:
                        out("   " + ln)
                except OSError:
                    out("Журнала пока нет.")
            out("")
            out("Готово. Если игра запущена, а оверлей ничего не видит — проверьте плагин общей памяти и режим игры «Окно без рамки».")

        threading.Thread(target=work, daemon=True).start()

    def reset(self):
        if not mb_yesno("Сброс", "Вернуть настройки лаунчера к стандартным? Автозапуск с Windows будет выключен."):
            return
        procs = self.app.cfg["procs"]
        self.app.cfg.update(default_launcher())
        self.app.cfg["procs"] = {g: DEFAULT_PROCS[g] for g in GAMES} if procs else self.app.cfg["procs"]
        set_win_start(False)
        self.app.save_cfg()
        self.app.root.after(30, self.app.rebuild)


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Yassno Overlay — лаунчер")
        sh = self.root.winfo_screenheight()
        sw = self.root.winfo_screenwidth()
        ww, wh = min(1580, max(1240, sw - 40)), min(1040, max(640, sh - 100))
        self.root.geometry(f"{ww}x{wh}+{max(0, (sw - ww) // 2)}+{max(0, (sh - wh - 40) // 2)}")
        self.root.minsize(1240, 640)
        self.win_h = wh
        self.cfg = load_launcher()
        self.runner = Runner()
        self.detected, self.found_proc = None, ""
        self.last_auto_game = None
        self._busy = False
        self.selected = self.cfg.get("last_game") if self.cfg.get("last_game") in GAMES else next(iter(GAMES))
        self.view = self.selected                          # игра или "settings"
        self.panels, self.tray = {}, None
        self.tray_q = queue.Queue()
        self.auto_var = tk.BooleanVar(value=self.cfg["autostart"])
        self.close_var = tk.BooleanVar(value=self.cfg["close_with_game"])
        self.apps_close_var = tk.BooleanVar(value=self.cfg["apps_close"])
        self.launch_var = tk.BooleanVar(value=self.cfg["launch_with_game"])
        self.app_vars = {k: tk.StringVar(value=self.cfg["app_paths"][k]) for k, _ in COMPANIONS}
        self.game_vars = {g: tk.StringVar(value=self.cfg["game_paths"][g]) for g in GAMES}
        for var in list(self.app_vars.values()) + list(self.game_vars.values()) + [self.apps_close_var, self.launch_var]:
            var.trace_add("write", lambda *a: self.on_paths_changed())
        self.companions = Companions()
        self.runner.on_start = self.after_start
        self.runner.on_stop = self.after_stop
        self.build()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.bind("<Unmap>", self.on_unmap)
        self.root.after(500, self.poll)
        self.root.after(1000, self.watch)
        self.root.after(300, self.pump)
        if "--minimized" in sys.argv or self.cfg.get("start_min_now"):
            self.root.after(400, self.start_hidden)
        elif self.cfg.get("auto_update", True):
            self.root.after(4000, lambda: self.check_update(False))

    # ---------- построение интерфейса ----------
    def build(self):
        set_ui_theme(self.cfg["theme"])
        set_lang(self.cfg["lang"])
        for g, p in self.panels.items():                   # не теряем несохранённые правки при пересборке
            try:
                p.save(quiet=True)
            except Exception:
                pass
        for w in self.root.winfo_children():
            w.destroy()
        setup_style(self.root)
        self.panels = {}
        tiles = ttk.Frame(self.root, padding=(14, 10, 14, 0))
        tiles.pack(fill="x")
        GameTile(tiles, tr("ИГРА"), lambda: None, blue=True, static=True).pack(side="left", padx=(0, 8), anchor="s")
        self.tiles = {}
        for game, name in GAMES.items():
            t = GameTile(tiles, name, lambda g=game: self.select_game(g), game=game)
            t.pack(side="left", padx=(0, 8), anchor="s")
            self.tiles[game] = t
        ttk.Button(tiles, text="Остановить оверлей", command=self.stop_overlay).pack(side="right", anchor="s", pady=(0, 2))
        ttk.Button(tiles, text="▶ Запустить игру", style="Accent.TButton", command=lambda: self.launch_game()).pack(side="right", anchor="s", padx=(0, 10), pady=(0, 2))

        head = ttk.Frame(self.root, padding=(14, 8, 14, 2))
        head.pack(fill="x")
        self.dot = tk.Canvas(head, width=16, height=16, bg=UI_BG, highlightthickness=0)
        self.dot.pack(side="left", padx=(0, 8))
        self.lbl_game = ttk.Label(head, text="", style="Head.TLabel")
        self.lbl_game.pack(side="left")
        self.lbl_overlay = ttk.Label(head, text="", style="Dim.TLabel")
        self.lbl_overlay.pack(side="left", padx=18)

        ttk.Button(head, text="Процессы игр…", command=self.edit_procs).pack(side="right")
        Toggle(head, "Закрывать оверлей вместе с игрой", self.close_var, command=self.save_cfg).pack(side="right", padx=(0, 22))
        Toggle(head, "Сам определять игру и запускать оверлей с её профилем", self.auto_var, command=self.save_cfg).pack(side="right", padx=(0, 22))

        body = ttk.Frame(self.root)
        body.pack(fill="both", expand=True, padx=14, pady=(2, 10))
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)
        self.navhost = tk.Frame(body, bg=UI_BG, width=210)
        self.navhost.grid(row=0, column=0, sticky="ns", padx=(0, 14), pady=(12, 12))
        self.navhost.pack_propagate(False)
        self.holder = ttk.Frame(body)
        self.holder.grid(row=0, column=1, sticky="nsew")
        for game in GAMES:
            p = ProfilePanel(self.holder, self, game)
            p.configure(padding=12)
            self.panels[game] = p
        self.settings_page = SettingsPage(self.holder, self)
        self.stats_page = StatsPage(self.holder, self)
        if self.view == "settings":
            self.show_settings()
        elif self.view == "stats":
            self.show_stats()
        else:
            self.select_game(self.selected)
        translate_tree(self.root)
        self.refresh_status()

    def rebuild(self):
        self.build()

    def select_game(self, game):
        self.selected, self.view = game, game
        if self.cfg.get("last_game") != game:
            self.cfg["last_game"] = game
            try:
                write_json(LAUNCHER_CFG, self.cfg)
            except Exception:
                pass
        self.settings_page.pack_forget()
        self.stats_page.pack_forget()
        for g, p in self.panels.items():
            if g == game:
                p.pack(fill="both", expand=True)
            else:
                p.pack_forget()
        self.update_tiles()

    def show_settings(self):
        self.view = "settings"
        for p in self.panels.values():
            p.pack_forget()
        self.stats_page.pack_forget()
        self.settings_page.pack(fill="both", expand=True)
        self.update_tiles()

    def show_stats(self):
        self.view = "stats"
        for p in self.panels.values():
            p.pack_forget()
        self.settings_page.pack_forget()
        self.stats_page.pack(fill="both", expand=True)
        self.stats_page.reload()
        self.update_tiles()

    def update_tiles(self):
        for g, t in self.tiles.items():
            t.set_state(g == self.view, g == self.detected)
        for g, p in self.panels.items():                    # левый список — от выбранной игры, подсветка — по текущей странице
            if g == self.selected:
                p.nav.pack(fill="both", expand=True)
                p.mark_view(self.view)
            else:
                p.nav.pack_forget()

    # ---------- настройки лаунчера ----------
    def on_paths_changed(self):
        self.save_cfg()
        for p in self.panels.values():
            try:
                p.update_app_status()
                p.changed()
            except tk.TclError:
                pass

    def browse_app(self, key):
        path = filedialog.askopenfilename(parent=self.root, title=tr("Выберите программу"),
                                          filetypes=[(tr("Программы и ярлыки"), "*.exe *.lnk *.bat *.cmd"), (tr("Все файлы"), "*.*")])
        if path:
            self.app_vars[key].set(os.path.normpath(path))

    def add_custom(self):
        path = filedialog.askopenfilename(parent=self.root, title=tr("Выберите программу"),
                                          filetypes=[(tr("Программы и ярлыки"), "*.exe *.lnk *.bat *.cmd"), (tr("Все файлы"), "*.*")])
        if not path:
            return
        game = self.selected if self.selected in GAMES else next(iter(GAMES))
        self.cfg["custom_apps"].append({"id": "c_" + uuid.uuid4().hex[:8], "name": os.path.splitext(os.path.basename(path))[0],
                                        "path": os.path.normpath(path), "games": [game]})
        self.save_cfg()
        self.reload_apps_pages()

    def remove_custom(self, cid):
        self.cfg["custom_apps"] = [c for c in self.cfg["custom_apps"] if c["id"] != cid]
        self.save_cfg()
        self.reload_apps_pages()

    def reload_apps_pages(self):
        for p in self.panels.values():
            try:
                if p.page == "apps":
                    p.show_page("apps")
                else:
                    p.changed()
            except tk.TclError:
                pass

    def browse_game(self, game):
        path = filedialog.askopenfilename(parent=self.root, title=tr("Выберите файл игры"),
                                          filetypes=[(tr("Программы и ярлыки"), "*.exe *.lnk *.bat *.cmd *.url"), (tr("Все файлы"), "*.*")])
        if path:
            self.game_vars[game].set(os.path.normpath(path))

    def say(self, text):
        p = self.panels.get(self.selected)
        if p is not None:
            p.say(text)

    def launch_game(self, game=None):
        game = game or self.selected
        if self.detected == game:
            self.say("Игра уже запущена.")
            return
        err = start_game_cmd(game, self.cfg)
        if err:
            mb_error("Не удалось запустить игру", err)
        else:
            r = self.runner
            if self.cfg.get("launch_with_game", True) and not (r.running() and r.game == game):
                if os.path.isfile(OVERLAY):
                    r.start(game, "", auto=True)
                self.say(f"Запускаю {GAMES[game]} и оверлей…")
            else:
                self.say(f"Запускаю {GAMES[game]}…")
            self.refresh_status()

    def after_start(self, game, mode, auto):
        """Оверлей запущен: поднимаем программы, отмеченные для этой игры (в фоне, чтобы окно не зависало)."""
        keys = [k for k, _ in companions_for(game) if companion_runs(k, game) and load_settings(game).get("app_" + k)]
        keys += [c["id"] for c in self.cfg.get("custom_apps", []) if game in c["games"]]
        if not keys:
            return
        cfg = dict(self.cfg)

        def work():
            problems = self.companions.start(keys, cfg)
            if problems:
                try:
                    self.root.after(0, lambda: self.say(" | ".join(problems)))
                except (RuntimeError, tk.TclError):
                    pass
        threading.Thread(target=work, daemon=True).start()

    def after_stop(self):
        if self.cfg.get("apps_close", True):
            self.companions.stop()

    def save_cfg(self):
        self.cfg["autostart"] = bool(self.auto_var.get())
        self.cfg["close_with_game"] = bool(self.close_var.get())
        self.cfg["apps_close"] = bool(self.apps_close_var.get())
        self.cfg["launch_with_game"] = bool(self.launch_var.get())
        self.cfg["app_paths"] = {k: v.get().strip() for k, v in self.app_vars.items()}
        self.cfg["game_paths"] = {g: v.get().strip() for g, v in self.game_vars.items()}
        try:
            write_json(LAUNCHER_CFG, self.cfg)
        except OSError:
            pass

    def edit_procs(self):
        win = tk.Toplevel(self.root)
        win.title(tr("Имена процессов игр"))
        win.configure(bg=UI_BG)
        win.transient(self.root)
        ttk.Label(win, text="Имена .exe через запятую — по ним лаунчер понимает, какая игра запущена.", style="Dim.TLabel").grid(row=0, column=0, columnspan=2, padx=12, pady=(12, 6))
        vars_ = {}
        for i, (g, n) in enumerate(GAMES.items()):
            ttk.Label(win, text=n).grid(row=i + 1, column=0, sticky="w", padx=12, pady=4)
            vars_[g] = tk.StringVar(value=self.cfg["procs"][g])
            ttk.Entry(win, textvariable=vars_[g], width=42).grid(row=i + 1, column=1, padx=12, pady=4)
        found = ttk.Label(win, text="Ищу похожие процессы…", style="Dim.TLabel", wraplength=460, justify="left")
        found.grid(row=4, column=0, columnspan=2, sticky="w", padx=12, pady=(8, 0))

        def fill():
            names = similar_processes()
            try:
                found.config(text=tr("Сейчас запущено похожее: ") + (", ".join(names) if names else tr("ничего не найдено (запустите игру и откройте окно заново)")))
            except tk.TclError:
                pass
        threading.Thread(target=fill, daemon=True).start()

        def ok():
            for g in GAMES:
                self.cfg["procs"][g] = vars_[g].get().strip() or DEFAULT_PROCS[g]
            self.save_cfg()
            win.destroy()
        ttk.Button(win, text="Сохранить", style="Accent.TButton", command=ok).grid(row=5, column=1, sticky="e", padx=12, pady=12)
        translate_tree(win)

    # ---------- определение игры ----------
    def poll(self):
        if not self._busy:
            self._busy = True
            procs = dict(self.cfg["procs"])

            def work():
                game, proc = detect_game(procs)
                try:
                    self.root.after(0, lambda: self.on_detect(game, proc))
                except RuntimeError:
                    pass
            threading.Thread(target=work, daemon=True).start()
        self.root.after(int(float(self.cfg.get("poll_sec", 2.5)) * 1000), self.poll)

    def on_detect(self, game, proc=""):
        self._busy = False
        changed = game != self.detected
        self.detected, self.found_proc = game, proc
        r = self.runner
        if self.auto_var.get():
            if game and game != self.last_auto_game:
                if not (r.running() and (not r.auto or r.game == game)):      # вручную запущенный или уже идущий оверлей не трогаем
                    r.start(game, "", auto=True)
                self.last_auto_game = game
            elif not game and self.last_auto_game:
                self.last_auto_game = None
                if self.close_var.get() and r.running() and r.auto:
                    r.stop()
        if game and changed and self.view not in ("settings", "stats"):
            self.select_game(game)
        self.refresh_status()

    def watch(self):
        self.refresh_status()
        if self.view == "stats":
            try:
                self.stats_page.auto_refresh()
            except tk.TclError:
                pass
        for g, p in self.panels.items():
            try:
                p.set_move_btn(p.move_active())
            except tk.TclError:
                pass
        self.root.after(1500, self.watch)

    def refresh_status(self):
        self.update_tiles()
        g = self.detected
        self.dot.delete("all")
        self.dot.create_oval(2, 2, 14, 14, fill=UI_GREEN if g else UI_OFF, outline="")
        self.lbl_game.config(text=f"{tr('Игра')}: {GAMES[g]}  ({self.found_proc})" if g else tr("Игра не запущена"))
        r = self.runner
        if r.running():
            extra = {"demo": tr(" (демо)"), "unlock": tr(" (режим перемещения)")}.get(r.mode, "")
            self.lbl_overlay.config(text=f"{tr('Оверлей: работает для')} {GAMES[r.game]}{extra}")
        else:
            self.lbl_overlay.config(text=tr("Оверлей: остановлен"))

    def stop_overlay(self):
        self.runner.stop()
        self.last_auto_game = self.detected                # чтобы не запускать сразу заново
        self.refresh_status()

    # ---------- трей и закрытие ----------
    def hide_to_tray(self):
        if not tray_supported():
            self.root.iconify()
            return False
        self.root.withdraw()
        if self.tray is None:
            try:
                self.tray = make_tray(lambda: self.tray_q.put("show"), lambda: self.tray_q.put("stop"),
                                      lambda: self.tray_q.put("quit"), None)
                self.tray.run_detached()
            except Exception as e:
                self.tray = None
                log_error("tray: " + repr(e))
                self.root.deiconify()
                return False
        return True

    def show_from_tray(self):
        if self.tray is not None:
            try:
                self.tray.stop()
            except Exception:
                pass
            self.tray = None
        self.root.deiconify()
        self.root.state("normal")
        self.root.lift()
        self.root.focus_force()

    def start_hidden(self):
        if self.cfg.get("min_tray") and tray_supported():
            self.hide_to_tray()
        else:
            self.root.iconify()

    def on_unmap(self, e):
        if e.widget is self.root and self.cfg.get("min_tray") and self.root.state() == "iconic":
            self.root.after_idle(self.hide_to_tray)

    def pump(self):
        try:
            while True:
                cmd = self.tray_q.get_nowait()
                if isinstance(cmd, tuple):                       # результат обновления из фонового потока
                    if cmd[0] == "upd_info":
                        self.on_update_info(*cmd[1:])
                    elif cmd[0] == "upd_done":
                        self.after_update(*cmd[1:])
                elif cmd == "show":
                    self.show_from_tray()
                elif cmd == "stop":
                    self.stop_overlay()
                elif cmd == "quit":
                    self.quit_app(stop=True)
                    return
        except queue.Empty:
            pass
        self.root.after(300, self.pump)

    # ---------- обновление из репозитория ----------
    def check_update(self, manual=False):
        def work():
            info, err = check_online_update()
            self.tray_q.put(("upd_info", info, err, manual))
        threading.Thread(target=work, daemon=True).start()

    def on_update_info(self, info, err, manual):
        if err:
            if manual:
                mb_error("Обновление", tr("Не удалось проверить обновления:") + "\n" + err)
            return
        if not info["newer"]:
            if manual:
                mb_info("Обновление", tr("У вас последняя версия: ") + APP_VERSION)
            return
        msg = (tr("Доступна версия ") + info["version"] + tr(" (у вас ") + APP_VERSION + ").\n\n"
               + (info["notes"] + "\n\n" if info["notes"] else "")
               + tr("Установить сейчас? Текущие файлы сохранятся в папке backup, настройки не меняются."))
        if messagebox.askyesno(tr("Обновление"), msg):
            def work():
                self.tray_q.put(("upd_done", info, apply_online_update(info)))
            threading.Thread(target=work, daemon=True).start()

    def after_update(self, info, err):
        if err:
            mb_error("Обновление", tr("Не удалось установить, файлы не изменены:") + "\n" + err)
            return
        try:
            self.settings_page.refresh_version()
        except Exception:
            pass
        mb_info("Обновление", tr("Установлена версия ") + info["version"] + tr(". Лаунчер перезапустится. Если оверлей запущен, он обновится при следующем запуске."))
        try:
            subprocess.Popen([sys.executable, os.path.join(HERE, "yassno_launcher.py")] + [a for a in sys.argv[1:] if a != "--minimized"],
                             cwd=HERE, creationflags=0x00000008 if os.name == "nt" else 0)
        except OSError as e:
            mb_error("Обновление", str(e))
            return
        self.quit_app(stop=False)

    def quit_app(self, stop=False):
        if stop:
            self.runner.stop()
            if self.cfg.get("apps_close", True):
                self.companions.stop()
        if self.tray is not None:
            try:
                self.tray.stop()
            except Exception:
                pass
        self.root.destroy()

    def on_close(self):
        if self.cfg.get("close_tray") and tray_supported():
            if self.hide_to_tray():
                return
        if self.runner.running():
            if self.cfg.get("stop_on_exit"):
                self.quit_app(stop=True)
                return
            ans = mb_yesnocancel("Выход", "Закрыть и оверлей тоже?\n\nДа — закрыть всё\nНет — оставить оверлей работать")
            if ans is None:
                return
            self.quit_app(stop=bool(ans))
            return
        self.quit_app()


def log_error(text):
    """Пишем ошибку в logs/launcher-error.log: при запуске через pythonw консоли нет и ошибок иначе не видно."""
    try:
        os.makedirs(LOGS, exist_ok=True)
        with open(os.path.join(LOGS, "launcher-error.log"), "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + "\n" + text + "\n")
    except OSError:
        pass


def main():
    if not os.path.isfile(OVERLAY):
        print("Рядом с лаунчером нет", os.path.basename(OVERLAY))
    try:
        app = App()
        app.root.report_callback_exception = lambda *exc: log_error("".join(traceback.format_exception(*exc)))
        app.root.mainloop()
    except Exception:
        text = traceback.format_exc()
        log_error(text)
        try:
            mb_error("Лаунчер не запустился", "Ошибка записана в logs/launcher-error.log:\n\n" + text[-1500:])
        except Exception:
            pass


if __name__ == "__main__":
    main()
