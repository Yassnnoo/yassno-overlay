"""Скачивание бесплатных сетапов Le Mans Ultimate из открытого репозитория сообщества (GitHub: seralaci/Le-Mans-Ultimate-Setups).

Оверлей пишет текущую трассу и машину в logs\\session.json, лаунчер по кнопке берёт отсюда подходящие .svm
и кладёт их в UserData\\player\\Settings\\<трасса>. Уже существующие файлы не трогаем."""
import json
import os
import re
import unicodedata
import urllib.parse
import urllib.request

REPO = "seralaci/Le-Mans-Ultimate-Setups"
BRANCH = "main"
TREE_URL = f"https://api.github.com/repos/{REPO}/git/trees/{BRANCH}?recursive=1"
RAW_URL = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/"

# (что встречается в названии трассы у игры, папка в репозитории; она же папка в UserData\player\Settings)
TRACKS = [
    (r"bahrain|sakhir", "Bahrain"),
    (r"americas|\bcota\b", "Circuit Of The Americas"),
    (r"fuji", "Fuji"),
    (r"imola|enzo e dino", "Imola"),
    (r"interlagos|carlos pace", "Interlagos"),
    (r"sarthe|le mans|lemans", "Lemans"),
    (r"monza", "Monza"),
    (r"portim|algarve", "Portimao"),
    (r"qatar|losail|lusail", "Qatar"),
    (r"sebring", "Sebring"),
    (r"silverstone", "Silverstone"),
    (r"\bspa\b|francorchamps", "Spa"),
]
# (что встречается в названии машины, что встречается в имени файла сетапа)
CARS = [
    (r"720s", r"720s"),
    (r"\bm4\b", r"\bm4\b"),
    (r"bmw.*hybrid|m hybrid", r"hy bmw"),
    (r"ligier|p325", r"ligier"),
]
# Снимок списка файлов репозитория на момент выпуска: запасной вариант, если GitHub API недоступен (лимит запросов без входа — 60 в час)
KNOWN_PATHS = [
 "Bahrain/BMW M4 Mid DF 5.2.svm",
 "Bahrain/HY BMW LDF Low DF level 4.1.svm",
 "Bahrain/LMP3 Ligier High DF lvl 3.1.svm",
 "Bahrain/SRA 720S evo High DF LVL1.6.svm",
 "Bahrain/SRA 720S evo High DF LVL3.6.svm",
 "Bahrain/SRA 720S evo High DF LVL5.6.svm",
 "Bahrain/SRA BMW M4 High DF LVL 4.5.svm",
 "Circuit Of The Americas/BMW M4 Mid DF 5.2.svm",
 "Circuit Of The Americas/HY BMW LDF Low DF level 4.1.svm",
 "Circuit Of The Americas/LMP3 Ligier High DF lvl 3.1.svm",
 "Circuit Of The Americas/SRA 720S evo High DF LVL1.6.svm",
 "Circuit Of The Americas/SRA 720S evo High DF LVL3.6.svm",
 "Circuit Of The Americas/SRA 720S evo High DF LVL5.6.svm",
 "Circuit Of The Americas/SRA BMW M4 High DF LVL 4.5.svm",
 "Fuji/BMW M4 Mid DF 5.2.svm",
 "Fuji/HY BMW LDF Low DF level 4.1.svm",
 "Fuji/LMP3 Ligier High DF lvl 3.1.svm",
 "Fuji/SRA 720S evo High DF LVL1.6.svm",
 "Fuji/SRA 720S evo High DF LVL3.6.svm",
 "Fuji/SRA 720S evo High DF LVL5.6.svm",
 "Fuji/SRA BMW M4 High DF LVL 4.5.svm",
 "Imola/BMW M4 Mid DF 5.2.svm",
 "Imola/HY BMW LDF Low DF level 4.1.svm",
 "Imola/LMP3 Ligier High DF lvl 3.1.svm",
 "Imola/SRA 720S evo High DF LVL1.6.svm",
 "Imola/SRA 720S evo High DF LVL3.6.svm",
 "Imola/SRA 720S evo High DF LVL5.6.svm",
 "Imola/SRA BMW M4 High DF LVL 4.5.svm",
 "Interlagos/BMW M4 Mid DF 5.2.svm",
 "Interlagos/HY BMW LDF Low DF level 4.1.svm",
 "Interlagos/LMP3 Ligier High DF lvl 3.1.svm",
 "Interlagos/SRA 720S evo High DF LVL1.6.svm",
 "Interlagos/SRA 720S evo High DF LVL3.6.svm",
 "Interlagos/SRA 720S evo High DF LVL5.6.svm",
 "Interlagos/SRA BMW M4 High DF LVL 4.5.svm",
 "Lemans/BMW M4 Mid DF 5.2.svm",
 "Lemans/HY BMW LDF Low DF level 4.1.svm",
 "Lemans/LMP3 Ligier High DF lvl 3.1.svm",
 "Lemans/SRA 720S evo High DF LVL1.6.svm",
 "Lemans/SRA 720S evo High DF LVL3.6.svm",
 "Lemans/SRA 720S evo High DF LVL5.6.svm",
 "Lemans/SRA BMW M4 High DF LVL 4.5.svm",
 "Monza/BMW M4 Mid DF 5.2.svm",
 "Monza/HY BMW LDF Low DF level 4.1.svm",
 "Monza/LMP3 Ligier High DF lvl 3.1.svm",
 "Monza/SRA 720S evo High DF LVL1.6.svm",
 "Monza/SRA 720S evo High DF LVL3.6.svm",
 "Monza/SRA 720S evo High DF LVL5.6.svm",
 "Monza/SRA BMW M4 High DF LVL 4.5.svm",
 "Portimao/BMW M4 Mid DF 5.2.svm",
 "Portimao/HY BMW LDF Low DF level 4.1.svm",
 "Portimao/LMP3 Ligier High DF lvl 3.1.svm",
 "Portimao/SRA 720S evo High DF LVL1.6.svm",
 "Portimao/SRA 720S evo High DF LVL3.6.svm",
 "Portimao/SRA 720S evo High DF LVL5.6.svm",
 "Portimao/SRA BMW M4 High DF LVL 4.5.svm",
 "Qatar/BMW M4 Mid DF 5.2.svm",
 "Qatar/HY BMW LDF Low DF level 4.1.svm",
 "Qatar/LMP3 Ligier High DF lvl 3.1.svm",
 "Qatar/SRA 720S evo High DF LVL1.6.svm",
 "Qatar/SRA 720S evo High DF LVL3.6.svm",
 "Qatar/SRA 720S evo High DF LVL5.6.svm",
 "Qatar/SRA BMW M4 High DF LVL 4.5.svm",
 "Sebring/BMW M4 Mid DF 5.2.svm",
 "Sebring/HY BMW LDF Low DF level 4.1.svm",
 "Sebring/LMP3 Ligier High DF lvl 3.1.svm",
 "Sebring/SRA 720S evo High DF LVL1.6.svm",
 "Sebring/SRA 720S evo High DF LVL3.6.svm",
 "Sebring/SRA 720S evo High DF LVL5.6.svm",
 "Sebring/SRA BMW M4 High DF LVL 4.5.svm",
 "Showroom/BMW M4 Mid DF 5.2.svm",
 "Showroom/HY BMW LDF Low DF level 4.1.svm",
 "Showroom/LMP3 Ligier High DF lvl 3.1.svm",
 "Showroom/SRA 720S evo High DF LVL1.6.svm",
 "Showroom/SRA 720S evo High DF LVL3.6.svm",
 "Showroom/SRA 720S evo High DF LVL5.6.svm",
 "Showroom/SRA BMW M4 High DF LVL 4.5.svm",
 "Silverstone/BMW M4 Mid DF 5.2.svm",
 "Silverstone/HY BMW LDF Low DF level 4.1.svm",
 "Silverstone/LMP3 Ligier High DF lvl 3.1.svm",
 "Silverstone/SRA 720S evo High DF LVL1.6.svm",
 "Silverstone/SRA 720S evo High DF LVL3.6.svm",
 "Silverstone/SRA 720S evo High DF LVL5.6.svm",
 "Silverstone/SRA BMW M4 High DF LVL 4.5.svm",
 "Spa/BMW M4 Mid DF 5.2.svm",
 "Spa/HY BMW LDF Low DF level 4.1.svm",
 "Spa/LMP3 Ligier High DF lvl 3.1.svm",
 "Spa/SRA 720S evo High DF LVL1.6.svm",
 "Spa/SRA 720S evo High DF LVL3.6.svm",
 "Spa/SRA 720S evo High DF LVL5.6.svm",
 "Spa/SRA BMW M4 High DF LVL 4.5.svm"
]

CARS_KNOWN = "BMW M4 GT3, McLaren 720S GT3 Evo, BMW M Hybrid V8, Ligier LMP3"


def _norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode("ascii")
    return s.lower()


def track_folder(track):
    t = _norm(track)
    for pat, folder in TRACKS:
        if re.search(pat, t):
            return folder
    return None


def car_pattern(vehicle):
    v = _norm(vehicle)
    for vpat, fpat in CARS:
        if re.search(vpat, v):
            return fpat
    return None


def fetch_json(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "yassno-overlay-setups", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_bytes(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "yassno-overlay-setups"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def read_session(logs_dir):
    """Что оверлей записал о текущей сессии: {'track', 'vehicle', 'game', 't'} или None."""
    try:
        with open(os.path.join(logs_dir, "session.json"), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def plan(paths, track, vehicle):
    """Какие файлы репозитория подходят: (папка_трассы, [пути]) или ошибка текстом.
    paths — все пути репозитория (строки вида 'Spa/BMW M4 Mid DF 5.2.svm')."""
    folder = track_folder(track)
    if not folder:
        return None, [], f"Трасса «{track}» не опознана — для неё в репозитории сетапов нет."
    pat = car_pattern(vehicle)
    if not pat:
        return folder, [], f"Для машины «{vehicle}» сетапов в репозитории нет (там есть: {CARS_KNOWN})."
    found = []
    for p in paths:
        parts = p.split("/")
        if len(parts) == 2 and parts[0] == folder and parts[1].lower().endswith(".svm") and re.search(pat, _norm(parts[1])):
            found.append(p)
    if not found:
        return folder, [], f"Для «{vehicle}» на трассе {folder} в репозитории сетапов нет."
    return folder, sorted(found), ""


def settings_track_dir(settings_root, folder):
    """Папка трассы в UserData\\player\\Settings: если такая уже есть (в любом регистре) — берём её."""
    try:
        for name in os.listdir(settings_root):
            if name.lower() == folder.lower() and os.path.isdir(os.path.join(settings_root, name)):
                return os.path.join(settings_root, name)
    except OSError:
        pass
    return os.path.join(settings_root, folder)


def looks_like_svm(data):
    head = data[:400].decode("utf-8", "ignore")
    return "VehicleClassSetting" in head or "UpgradeSetting" in head


def download(settings_root, track, vehicle, tree_fn=None, get_fn=None):
    """Скачивает подходящие сетапы. Возвращает словарь: ok, message, saved, skipped, folder."""
    tree_fn = tree_fn or fetch_json
    get_fn = get_fn or fetch_bytes
    try:
        tree = tree_fn(TREE_URL)
        paths = [t["path"] for t in tree.get("tree", []) if t.get("type") == "blob"]
        if not paths:
            raise ValueError("пустой список")
    except Exception:
        paths = list(KNOWN_PATHS)                                    # GitHub API недоступен или лимит — берём список из снимка
    folder, files, err = plan(paths, track, vehicle)
    if err:
        return {"ok": False, "message": err, "saved": [], "skipped": [], "folder": folder}
    dest = settings_track_dir(settings_root, folder)
    os.makedirs(dest, exist_ok=True)
    saved, skipped = [], []
    for p in files:
        name = p.split("/", 1)[1]
        target = os.path.join(dest, name)
        if os.path.exists(target):
            skipped.append(name)
            continue
        data = get_fn(RAW_URL + urllib.parse.quote(p))
        if not looks_like_svm(data):
            raise ValueError(f"Файл {name} скачался не как сетап — пропускаю.")
        with open(target, "wb") as f:
            f.write(data)
        saved.append(name)
    msg = f"{folder}: скачано {len(saved)}"
    if skipped:
        msg += f", уже были {len(skipped)} (не тронуты)"
    return {"ok": True, "message": msg, "saved": saved, "skipped": skipped, "folder": dest}
