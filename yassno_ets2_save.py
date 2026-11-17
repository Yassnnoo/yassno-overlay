"""Yassno Overlay — чтение профиля ETS 2 из файлов сохранений (без сторонних библиотек).

Игра шифрует game.sii/profile.sii (формат ScsC: AES-256-CBC + zlib; ключ публичный и один для всех),
внутри — бинарный BSII или обычный текст. Читаем только начало файла: деньги, опыт, пробег и т.п.
"""
import glob
import os
import re
import struct
import threading
import zlib

# ---------------- AES-256 (только расшифровка, CBC) ----------------
_SBOX = []
_INV = [0] * 256


def _init_tables():
    p = q = 1
    sb = [0] * 256
    while True:                                   # перебор элементов GF(2^8) — стандартный способ построить S-box
        p = p ^ ((p << 1) & 0xFF) ^ (0x1B if p & 0x80 else 0)
        q ^= q << 1
        q ^= q << 2
        q ^= q << 4
        q &= 0xFF
        if q & 0x80:
            q ^= 0x09
        x = q ^ (q << 1 | q >> 7) & 0xFF ^ (q << 2 | q >> 6) & 0xFF ^ (q << 3 | q >> 5) & 0xFF ^ (q << 4 | q >> 4) & 0xFF
        sb[p] = (x ^ 0x63) & 0xFF
        if p == 1:
            break
    sb[0] = 0x63
    return sb


def _mul(a, b):
    r = 0
    while b:
        if b & 1:
            r ^= a
        a = ((a << 1) ^ 0x11B) if a & 0x80 else a << 1
        b >>= 1
    return r & 0xFF


_SBOX = _init_tables()
for _i, _v in enumerate(_SBOX):
    _INV[_v] = _i
_TD = [[0] * 256 for _ in range(4)]
for _i in range(256):
    s = _INV[_i]
    w = (_mul(s, 14) << 24) | (_mul(s, 9) << 16) | (_mul(s, 13) << 8) | _mul(s, 11)
    for _k in range(4):
        _TD[_k][_i] = ((w >> (8 * _k)) | (w << (32 - 8 * _k))) & 0xFFFFFFFF


def _expand(key):
    nk = len(key) // 4
    nr = nk + 6
    w = list(struct.unpack(">%dI" % nk, key))
    rc = 1
    for i in range(nk, 4 * (nr + 1)):
        t = w[i - 1]
        if i % nk == 0:
            t = ((t << 8) | (t >> 24)) & 0xFFFFFFFF
            t = (_SBOX[t >> 24] << 24) | (_SBOX[(t >> 16) & 255] << 16) | (_SBOX[(t >> 8) & 255] << 8) | _SBOX[t & 255]
            t ^= rc << 24
            rc = _mul(rc, 2)
        elif nk > 6 and i % nk == 4:
            t = (_SBOX[t >> 24] << 24) | (_SBOX[(t >> 16) & 255] << 16) | (_SBOX[(t >> 8) & 255] << 8) | _SBOX[t & 255]
        w.append(w[i - nk] ^ t)
    dk = []                                       # ключ для «эквивалентного обратного шифра»
    for r in range(nr + 1):
        blk = w[4 * (nr - r):4 * (nr - r) + 4]
        if 0 < r < nr:
            nb = []
            for x in blk:
                nb.append(_TD[0][_SBOX[x >> 24]] ^ _TD[1][_SBOX[(x >> 16) & 255]] ^ _TD[2][_SBOX[(x >> 8) & 255]] ^ _TD[3][_SBOX[x & 255]])
            blk = nb
        dk.extend(blk)
    return dk, nr


def aes_cbc_decrypt(key, iv, data):
    dk, nr = _expand(key)
    t0, t1, t2, t3 = _TD
    inv = _INV
    out = bytearray()
    prev = struct.unpack(">4I", iv)
    unpack = struct.Struct(">4I").unpack_from
    pack = struct.Struct(">4I").pack
    for off in range(0, len(data) - 15, 16):
        c = unpack(data, off)
        s0, s1, s2, s3 = c[0] ^ dk[0], c[1] ^ dk[1], c[2] ^ dk[2], c[3] ^ dk[3]
        k = 4
        for _ in range(nr - 1):
            n0 = t0[s0 >> 24] ^ t1[(s3 >> 16) & 255] ^ t2[(s2 >> 8) & 255] ^ t3[s1 & 255] ^ dk[k]
            n1 = t0[s1 >> 24] ^ t1[(s0 >> 16) & 255] ^ t2[(s3 >> 8) & 255] ^ t3[s2 & 255] ^ dk[k + 1]
            n2 = t0[s2 >> 24] ^ t1[(s1 >> 16) & 255] ^ t2[(s0 >> 8) & 255] ^ t3[s3 & 255] ^ dk[k + 2]
            n3 = t0[s3 >> 24] ^ t1[(s2 >> 16) & 255] ^ t2[(s1 >> 8) & 255] ^ t3[s0 & 255] ^ dk[k + 3]
            s0, s1, s2, s3 = n0, n1, n2, n3
            k += 4
        r0 = ((inv[s0 >> 24] << 24) | (inv[(s3 >> 16) & 255] << 16) | (inv[(s2 >> 8) & 255] << 8) | inv[s1 & 255]) ^ dk[k]
        r1 = ((inv[s1 >> 24] << 24) | (inv[(s0 >> 16) & 255] << 16) | (inv[(s3 >> 8) & 255] << 8) | inv[s2 & 255]) ^ dk[k + 1]
        r2 = ((inv[s2 >> 24] << 24) | (inv[(s1 >> 16) & 255] << 16) | (inv[(s0 >> 8) & 255] << 8) | inv[s3 & 255]) ^ dk[k + 2]
        r3 = ((inv[s3 >> 24] << 24) | (inv[(s2 >> 16) & 255] << 16) | (inv[(s1 >> 8) & 255] << 8) | inv[s0 & 255]) ^ dk[k + 3]
        out += pack(r0 ^ prev[0], r1 ^ prev[1], r2 ^ prev[2], r3 ^ prev[3])
        prev = c
    return bytes(out)


SII_KEY = bytes([0x2a, 0x5f, 0xcb, 0x17, 0x91, 0xd2, 0x2f, 0xb6, 0x02, 0x45, 0xb3, 0xd8, 0x36, 0x9e, 0xd0, 0xb2,
                 0xc2, 0x73, 0x71, 0x56, 0x3f, 0xbf, 0x1f, 0x3c, 0x9e, 0xdf, 0x6b, 0x11, 0x82, 0x5a, 0x5d, 0x0a])


def read_sii(path, limit=700_000):
    """Начало SII-файла (расшифрованное и распакованное): bytes или None."""
    with open(path, "rb") as f:
        head = f.read(56)
        if head[:4] != b"ScsC":
            f.seek(0)
            return f.read(limit)
        iv = head[36:52]
        dec = zlib.decompressobj()
        out = b""
        while len(out) < limit:
            chunk = f.read(32768)
            if not chunk:
                break
            chunk = chunk[:len(chunk) // 16 * 16]
            if not chunk:
                break
            out += dec.decompress(aes_cbc_decrypt(SII_KEY, iv, chunk))
            iv = chunk[-16:]
        return out


# ---------------- BSII ----------------
_FIX = {0x05: "<f", 0x07: "<2f", 0x09: "<3f", 0x11: "<3i", 0x17: "<4f", 0x19: "<8f", 0x25: "<i", 0x27: "<I", 0x29: "<H",
        0x2B: "<H", 0x2F: "<?", 0x31: "<q", 0x33: "<Q", 0x35: "<?", 0x03: "<Q", 0x37: "<I", 0x3D: "<I"}


def parse_bsii(b, stop_after=("player",)):
    """Разбор BSII до юнита из stop_after. Возвращает список (имя_типа, поля). Незнакомый тип поля — тихо останавливаемся."""
    if b[:4] != b"BSII":
        return []
    p = 8
    structs, units = {}, []

    def rd(fmt):
        nonlocal p
        v = struct.unpack_from(fmt, b, p)
        p += struct.calcsize(fmt)
        return v if len(v) > 1 else v[0]

    def string():
        nonlocal p
        n = rd("<I")
        s = b[p:p + n].decode("utf8", "replace")
        p += n
        return s

    def ident():
        nonlocal p
        n = b[p]
        p += 1
        if n == 0xFF:
            return rd("<Q")
        for _ in range(n):
            rd("<Q")
        return None

    def val(t):
        if t == 0x01:
            return string()
        if t in (0x39, 0x3B):
            return ident()
        if t in _FIX:
            return rd(_FIX[t])
        if t in (0x3A, 0x3C):
            return [ident() for _ in range(rd("<I"))]
        if t == 0x02:
            return [string() for _ in range(rd("<I"))]
        if (t - 1) in _FIX:
            return [val(t - 1) for _ in range(rd("<I"))]
        raise ValueError(t)

    try:
        while p < len(b):
            t = rd("<I")
            if t == 0:
                if not b[p]:
                    break
                p += 1
                sid = rd("<I")
                name = string()
                fields = []
                while True:
                    ft = rd("<I")
                    if not ft:
                        break
                    fn = string()
                    tab = None
                    if ft == 0x37:
                        tab = {}
                        for _ in range(rd("<I")):
                            k = rd("<I")
                            tab[k] = string()
                    fields.append((ft, fn, tab))
                structs[sid] = (name, fields)
            else:
                name, fields = structs[t]
                ident()
                d = {}
                for ft, fn, tab in fields:
                    v = val(ft)
                    d[fn] = tab.get(v, v) if tab else v
                units.append((name, d))
                if name in stop_after:
                    break
    except (ValueError, KeyError, struct.error, IndexError):
        pass
    return units


_TXT = {"money": r"money_account:\s*(-?\d+)", "xp": r"experience_points:\s*(\d+)", "cancelled": r"cancelled_job_count:\s*(\d+)",
        "fuel": r"total_fuel_litres:\s*(\d+)", "cities": r"visited_cities_count:\s*(\d+)", "km": r"total_distances_by_mode\[0\]:\s*(\d+)"}


def read_game(path):
    """Статистика из game.sii: dict с money/xp/km/cancelled/fuel/cities/debt или None."""
    raw = read_sii(path)
    if not raw:
        return None
    r = {}
    if raw[:4] == b"BSII":
        eco = bank = None
        debt = 0
        for name, d in parse_bsii(raw):
            if name == "economy":
                eco = d
            elif name == "bank":
                bank = d
            elif name == "bank_loan":
                debt += int(d.get("amount") or 0)
        if not eco or not bank:
            return None
        km = (eco.get("total_distances_by_mode") or [0])[0] or eco.get("total_distance") or 0
        r = {"money": bank.get("money_account"), "xp": eco.get("experience_points"), "km": km,
             "cancelled": eco.get("cancelled_job_count"), "fuel": eco.get("total_fuel_litres"),
             "cities": eco.get("visited_cities_count"), "debt": debt}
        if isinstance(r["cities"], list):
            r["cities"] = len(r["cities"])
    else:
        txt = raw.decode("utf8", "replace")
        for k, rx in _TXT.items():
            m = re.search(rx, txt)
            if m:
                r[k] = int(m.group(1))
        if "money" not in r:
            return None
        r["debt"] = sum(int(x) for x in re.findall(r"^\s*amount:\s*(\d+)", txt, re.M))
    return r


def read_profile_name(profile_dir):
    p = os.path.join(profile_dir, "profile.sii")
    try:
        txt = (read_sii(p, 20000) or b"").decode("utf8", "replace")
    except OSError:
        return ""
    m = re.search(r'company_name:\s*"((?:[^"\\]|\\.)*)"', txt)
    if not m:
        return ""
    s = m.group(1)
    try:
        return re.sub(r"(?:\\x[0-9a-fA-F]{2})+", lambda mm: bytes.fromhex(mm.group(0).replace("\\x", "")).decode("utf8", "replace"), s)
    except ValueError:
        return s


# ---------------- поиск сохранений ----------------
def documents_dirs():
    res = []
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0:
            res.append(buf.value)
    except Exception:                              # noqa: BLE001 — не Windows
        pass
    home = os.path.expanduser("~")
    res += [os.path.join(home, "Documents"), os.path.join(home, "OneDrive", "Documents"), os.path.join(home, "OneDrive", "Документы")]
    out = []
    for d in res:
        if d and d not in out and os.path.isdir(d):
            out.append(d)
    return out


def save_time(game_path):
    """Время записи сохранения: file_time из info.sii (у копий .bak mtime у всех одинаковый), иначе mtime файла."""
    try:
        txt = (read_sii(os.path.join(os.path.dirname(game_path), "info.sii"), 20000) or b"").decode("utf8", "replace")
        m = re.search(r"file_time:\s*(\d+)", txt)
        if m:
            return float(m.group(1))
    except (OSError, zlib.error, struct.error):
        pass
    return os.path.getmtime(game_path)


def profile_nick(profile_dir):
    """Имя профиля: папки steam_profiles названы hex-строкой UTF-8 (…666C6164… = «Vladimir …»)."""
    n = os.path.basename(profile_dir)
    try:
        return bytes.fromhex(n).decode("utf8") if len(n) % 2 == 0 else n
    except ValueError:
        return n


def cloud_bases(steam=None):
    """Профили Steam Cloud: <Steam>\\userdata\\<аккаунт>\\227300\\remote (227300 — Euro Truck Simulator 2)."""
    res = []
    for d in (steam if steam is not None else steam_dirs()):
        res += glob.glob(os.path.join(d, "userdata", "*", "227300", "remote"))
    return res


def find_saves(docs=None, steam=None):
    """[(mtime, game.sii, profile_dir, stale)] — свежие сначала. stale=True — копии в *.bak."""
    found = []
    bases = [os.path.join(d, "Euro Truck Simulator 2") for d in (docs if docs is not None else documents_dirs())]
    bases += cloud_bases(steam)
    for base in bases:
        for pat in ("profiles*", "steam_profiles*"):
            for root in glob.glob(os.path.join(base, pat)):
                stale = root.lower().endswith(".bak") or ".bak" in os.path.basename(root).lower()
                for g in glob.glob(os.path.join(root, "*", "save", "*", "game.sii")):
                    try:
                        found.append((save_time(g), g, os.path.dirname(os.path.dirname(os.path.dirname(g))), stale))
                    except OSError:
                        pass
    found.sort(key=lambda x: (x[3], -x[0]))
    return found


_cache = {}
_lock = threading.Lock()


def load_stats(docs=None, tries=1, steam=None):
    """Статистика самого свежего сохранения (по file_time из info.sii; настоящие профили раньше копий .bak) или None.
    Результат кэшируется по файлу."""
    saves = find_saves(docs, steam)
    if not saves:
        return None
    best = None
    stale_flag = saves[0][3]
    pool = [s for s in saves if s[3] == stale_flag][:tries]
    for mt, path, pdir, stale in pool:
        try:
            key = (path, mt, os.path.getsize(path))
        except OSError:
            continue
        with _lock:
            st = _cache.get(key)
        if st is None:
            try:
                st = read_game(path)
            except (OSError, zlib.error, struct.error, ValueError):
                st = None
            if st is not None:
                st = dict(st, name=read_profile_name(pdir), nick=profile_nick(pdir), save=os.path.basename(os.path.dirname(path)), mtime=mt, stale=stale, path=path)
            with _lock:
                _cache[key] = st or {}
        if st and (best is None or (st.get("km") or 0, mt) > (best.get("km") or 0, best["mtime"])):
            best = st
    return best


# ---------------- аватарка Steam ----------------
def steam_dirs():
    out = []
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            out.append(winreg.QueryValueEx(k, "SteamPath")[0].replace("/", "\\"))
    except Exception:                              # noqa: BLE001 — не Windows или Steam не стоит
        pass
    for env in ("ProgramFiles(x86)", "ProgramFiles"):
        if os.environ.get(env):
            out.append(os.path.join(os.environ[env], "Steam"))
    return [d for i, d in enumerate(out) if d and d not in out[:i] and os.path.isdir(d)]


def steam_avatar(dirs=None):
    """Путь к png-аватарке активного аккаунта Steam (из avatarcache) или None."""
    for d in (dirs if dirs is not None else steam_dirs()):
        try:
            with open(os.path.join(d, "config", "loginusers.vdf"), encoding="utf-8", errors="replace") as f:
                txt = f.read()
        except OSError:
            continue
        users = re.findall(r'"(7656\d{13})"\s*\{(.*?)\n\s*\}', txt, re.S)
        users.sort(key=lambda u: '"MostRecent"\t\t"1"' not in u[1] and not re.search(r'"MostRecent"\s+"1"', u[1]))
        for sid, _ in users:
            p = os.path.join(d, "config", "avatarcache", sid + ".png")
            if os.path.isfile(p):
                return p
    return None
