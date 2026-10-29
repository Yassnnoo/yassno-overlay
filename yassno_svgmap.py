"""Схемы трасс iRacing: скачивает SVG с публичного сайта iRacing и превращает их в линию трассы для карты.

Ничего из iRacing в программу не вшито: файлы скачиваются на компьютер пользователя при первой гонке на трассе
и лежат в папке maps/svg. Если что-то не получилось (нет сети, другой адрес, необычная трасса), возвращается None,
и оверлей рисует карту по траектории игрока, как раньше.
"""
import json
import math
import os
import re
import threading
import urllib.request

BASE = "https://members-assets.iracing.com/public/track-maps/"
N_POINTS = 2000
_NUM = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_TOK = re.compile(r"([MmLlHhVvCcSsQqTtAaZz])|([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)")


# ---------------------------------------------------------------- разбор SVG
def _bez3(p0, p1, p2, p3, n=14):
    out = []
    for i in range(1, n + 1):
        t = i / n
        u = 1.0 - t
        out.append((u * u * u * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t * t * t * p3[0],
                    u * u * u * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t * t * t * p3[1]))
    return out


def _bez2(p0, p1, p2, n=10):
    out = []
    for i in range(1, n + 1):
        t = i / n
        u = 1.0 - t
        out.append((u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0], u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1]))
    return out


def _arc(p0, rx, ry, rot, large, sweep, p1, n=16):
    """Дуга SVG (A) -> точки."""
    if rx == 0 or ry == 0 or p0 == p1:
        return [p1]
    rx, ry = abs(rx), abs(ry)
    phi = math.radians(rot)
    cp, sp = math.cos(phi), math.sin(phi)
    dx, dy = (p0[0] - p1[0]) / 2.0, (p0[1] - p1[1]) / 2.0
    x1, y1 = cp * dx + sp * dy, -sp * dx + cp * dy
    lam = x1 * x1 / (rx * rx) + y1 * y1 / (ry * ry)
    if lam > 1:
        s = math.sqrt(lam)
        rx, ry = rx * s, ry * s
    num = rx * rx * ry * ry - rx * rx * y1 * y1 - ry * ry * x1 * x1
    den = rx * rx * y1 * y1 + ry * ry * x1 * x1
    co = math.sqrt(max(0.0, num / den)) if den else 0.0
    if bool(large) == bool(sweep):
        co = -co
    cxp, cyp = co * rx * y1 / ry, -co * ry * x1 / rx
    cx = cp * cxp - sp * cyp + (p0[0] + p1[0]) / 2.0
    cy = sp * cxp + cp * cyp + (p0[1] + p1[1]) / 2.0
    a0 = math.atan2((y1 - cyp) / ry, (x1 - cxp) / rx)
    a1 = math.atan2((-y1 - cyp) / ry, (-x1 - cxp) / rx)
    da = a1 - a0
    if sweep and da < 0:
        da += 2 * math.pi
    elif not sweep and da > 0:
        da -= 2 * math.pi
    out = []
    for i in range(1, n + 1):
        a = a0 + da * i / n
        ex, ey = rx * math.cos(a), ry * math.sin(a)
        out.append((cp * ex - sp * ey + cx, sp * ex + cp * ey + cy))
    return out


def path_contours(d):
    """Атрибут d элемента path -> список подконтуров (каждый — список точек)."""
    toks = [(m.group(1), m.group(2)) for m in _TOK.finditer(d)]
    i, n = 0, len(toks)
    subs, cur = [], []
    pos = start = (0.0, 0.0)
    cmd, last_c, last_q = None, None, None

    def num():
        nonlocal i
        if i >= n or toks[i][1] is None:
            raise ValueError("path: ждали число")
        v = float(toks[i][1])
        i += 1
        return v

    def has_num():
        return i < n and toks[i][1] is not None

    while i < n:
        if toks[i][0]:
            cmd = toks[i][0]
            i += 1
            if cmd in "Zz":
                if cur:
                    subs.append(cur)
                cur, pos, last_c, last_q = [], start, None, None
                continue
        elif cmd is None:
            raise ValueError("path: нет команды")
        rel = cmd.islower()
        c = cmd.upper()
        ox, oy = pos if rel else (0.0, 0.0)
        if c == "M":
            if cur:
                subs.append(cur)
            x, y = num() + ox, num() + oy
            pos = start = (x, y)
            cur = [pos]
            cmd = "l" if rel else "L"
            last_c = last_q = None
        elif c == "L":
            pos = (num() + ox, num() + oy)
            cur.append(pos)
            last_c = last_q = None
        elif c == "H":
            pos = (num() + (pos[0] if rel else 0.0), pos[1])
            cur.append(pos)
            last_c = last_q = None
        elif c == "V":
            pos = (pos[0], num() + (pos[1] if rel else 0.0))
            cur.append(pos)
            last_c = last_q = None
        elif c == "C":
            p1 = (num() + ox, num() + oy)
            p2 = (num() + ox, num() + oy)
            p3 = (num() + ox, num() + oy)
            cur.extend(_bez3(pos, p1, p2, p3))
            pos, last_c, last_q = p3, p2, None
        elif c == "S":
            p1 = (2 * pos[0] - last_c[0], 2 * pos[1] - last_c[1]) if last_c else pos
            p2 = (num() + ox, num() + oy)
            p3 = (num() + ox, num() + oy)
            cur.extend(_bez3(pos, p1, p2, p3))
            pos, last_c, last_q = p3, p2, None
        elif c == "Q":
            p1 = (num() + ox, num() + oy)
            p2 = (num() + ox, num() + oy)
            cur.extend(_bez2(pos, p1, p2))
            pos, last_q, last_c = p2, p1, None
        elif c == "T":
            p1 = (2 * pos[0] - last_q[0], 2 * pos[1] - last_q[1]) if last_q else pos
            p2 = (num() + ox, num() + oy)
            cur.extend(_bez2(pos, p1, p2))
            pos, last_q, last_c = p2, p1, None
        elif c == "A":
            rx, ry, rot, la, sw = num(), num(), num(), num(), num()
            p1 = (num() + ox, num() + oy)
            cur.extend(_arc(pos, rx, ry, rot, la, sw, p1))
            pos, last_c, last_q = p1, None, None
        if not has_num() and i < n and toks[i][0] is None:
            break
    if cur:
        subs.append(cur)
    return subs


def _svg_paths(text):
    return [m.group(1) for m in re.finditer(r'<path\b[^>]*?\sd="([^"]+)"', text)]


def _area(p):
    return 0.5 * sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(p, p[1:] + p[:1]))


def _length(p, closed=True):
    q = p + p[:1] if closed else p
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(q, q[1:]))


def resample(p, n):
    """Замкнутая ломаная -> n точек, равномерно по длине."""
    q = p + p[:1]
    cum = [0.0]
    for a, b in zip(q, q[1:]):
        cum.append(cum[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
    total = cum[-1]
    if total <= 0:
        raise ValueError("нулевая длина")
    out, j = [], 0
    for k in range(n):
        t = total * k / n
        while j < len(q) - 2 and cum[j + 1] < t:
            j += 1
        seg = (cum[j + 1] - cum[j]) or 1.0
        u = (t - cum[j]) / seg
        out.append((q[j][0] + (q[j + 1][0] - q[j][0]) * u, q[j][1] + (q[j + 1][1] - q[j][1]) * u))
    return out


def centerline(contours):
    """Два контура ленты трассы -> средняя линия (замкнутая ломаная). None, если лента необычная."""
    cs = [c for c in contours if len(c) >= 8 and _length(c) > 200]
    if len(cs) != 2:
        return None
    a, b = cs
    la, lb = _length(a), _length(b)
    if not 0.4 < la / lb < 2.5:
        return None
    n = 1200
    A = resample(a, n)

    def match(bpts):
        """Соответствие точек A точкам B: ближайшая, поиск рядом с предыдущей (чтобы не перескочить на соседний участок)."""
        B = resample(bpts, n)

        def nearest(p, lo, hi):
            best, bj = 1e18, lo
            for j in range(lo, hi):
                q = B[j % n]
                d = (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2
                if d < best:
                    best, bj = d, j
            return bj
        js = [nearest(A[0], 0, n)]
        for i in range(1, n):
            js.append(max(nearest(A[i], js[-1] - 4, js[-1] + 30), js[-1] - 1))
        width = sum(math.hypot(A[i][0] - B[js[i] % n][0], A[i][1] - B[js[i] % n][1]) for i in range(n)) / n
        return B, js, width

    best = None
    for cand in (b, b[::-1]):
        B, js, width = match(cand)
        travel = js[-1] - js[0]
        if abs(travel - n) > n * 0.08:
            continue                                     # B не прошёл один круг вместе с A
        if best is None or width < best[2]:
            best = (B, js, width)
    if best is None:
        return None
    B, js, width = best
    mid = []
    for i in range(n):
        q = B[js[i] % n]
        mid.append(((A[i][0] + q[0]) / 2.0, (A[i][1] + q[1]) / 2.0))
    length = _length(mid)
    if width > 0.06 * length:
        return None                                      # «лента» шире 6% длины круга — это не трасса
    sm = []
    for i in range(n):                                   # лёгкое сглаживание
        pts = [mid[(i + k) % n] for k in range(-3, 4)]
        sm.append((sum(p[0] for p in pts) / 7.0, sum(p[1] for p in pts) / 7.0))
    return sm


def parse_start_finish(text):
    """Линия старта/финиша и стрелка направления -> ((x, y) центр линии, (dx, dy) направление) или None."""
    rect = re.search(r'<rect\b[^>]*?x="([-\d.]+)"[^>]*?y="([-\d.]+)"[^>]*?width="([-\d.]+)"[^>]*?height="([-\d.]+)"', text)
    if not rect:
        rect = re.search(r'<rect\b[^>]*?width="([-\d.]+)"[^>]*?height="([-\d.]+)"[^>]*?x="([-\d.]+)"[^>]*?y="([-\d.]+)"', text)
        if rect:
            w, h, x, y = (float(v) for v in rect.groups())
        else:
            return None
    else:
        x, y, w, h = (float(v) for v in rect.groups())
    cx, cy = x + w / 2.0, y + h / 2.0
    poly = re.search(r'<polygon\b[^>]*?points="([^"]+)"', text)
    if not poly:
        return (cx, cy), None
    nums = [float(v) for v in _NUM.findall(poly.group(1))]
    pts = list(zip(nums[0::2], nums[1::2]))
    if len(pts) > 2 and pts[0] == pts[-1]:
        pts = pts[:-1]
    if len(pts) < 3:
        return (cx, cy), None
    mx, my = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
    tip = max(pts, key=lambda p: (p[0] - mx) ** 2 + (p[1] - my) ** 2)
    dx, dy = tip[0] - mx, tip[1] - my
    nrm = math.hypot(dx, dy)
    return (cx, cy), ((dx / nrm, dy / nrm) if nrm else None)


def build_loop(active_svg, start_finish_svg=None, n=N_POINTS):
    """SVG слоёв -> n точек средней линии трассы (в координатах SVG), первая — линия старта/финиша, направление — по стрелке."""
    contours = []
    for d in _svg_paths(active_svg):
        contours.extend(path_contours(d))
    mid = centerline(contours)
    if not mid:
        return None
    loop = resample(mid, n)
    sf = parse_start_finish(start_finish_svg) if start_finish_svg else None
    if sf:
        (cx, cy), arrow = sf
        k = min(range(n), key=lambda i: (loop[i][0] - cx) ** 2 + (loop[i][1] - cy) ** 2)
        if arrow:
            a, b = loop[(k - 8) % n], loop[(k + 8) % n]
            if (b[0] - a[0]) * arrow[0] + (b[1] - a[1]) * arrow[1] < 0:
                loop = loop[::-1]
                k = n - 1 - k
        loop = loop[k:] + loop[:k]
    return loop


# ---------------------------------------------------------------- адреса и загрузка
def candidates(track_id, track_name):
    """Возможные папки схемы: tracks_<первое слово>/<id>-<название через дефисы>."""
    name = (track_name or "").strip().lower()
    if not name or not track_id:
        return []
    out = []
    flat = re.sub(r"[^a-z0-9]+", "-", name).strip("-")
    first = re.split(r"[^a-z0-9]+", name)[0]
    first2 = re.sub(r"[^a-z0-9]+", "", name.split(" ")[0])
    for head in dict.fromkeys([first, first2]):
        if head:
            out.append(f"tracks_{head}/{int(track_id)}-{flat}")
    return out


def _get(url, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 YassnoOverlay"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


class IrSvgMaps:
    """Кэш схем: get() сразу возвращает готовую линию или None и в фоне пытается скачать."""

    def __init__(self, folder):
        self.folder = folder
        self.loops = {}                    # track_id -> список точек | False (не вышло)
        self.world = {}                    # track_id -> (длина трассы, точки в метрах)
        self.busy = set()
        self.lock = threading.Lock()

    def _file(self, track_id):
        return os.path.join(self.folder, f"{int(track_id)}.json")

    def get(self, track_id, track_name):
        if not track_id:
            return None
        with self.lock:
            v = self.loops.get(track_id)
            if v is not None:
                return v or None
            if track_id in self.busy:
                return None
            self.busy.add(track_id)
        threading.Thread(target=self._work, args=(track_id, track_name), daemon=True).start()
        return None

    def _work(self, track_id, track_name):
        result = False
        try:
            try:
                with open(self._file(track_id), encoding="utf-8") as f:
                    d = json.load(f)
                pts = [tuple(p) for p in d["points"]]
                if len(pts) >= 200:
                    result = pts
            except (OSError, ValueError, KeyError, TypeError):
                pass
            if not result:
                for folder in candidates(track_id, track_name):
                    try:
                        active = _get(f"{BASE}{folder}/active.svg")
                    except Exception:
                        continue
                    try:
                        sf = _get(f"{BASE}{folder}/start-finish.svg")
                    except Exception:
                        sf = None
                    try:
                        pts = build_loop(active, sf)
                    except Exception:
                        pts = None
                    if pts:
                        result = pts
                        try:
                            os.makedirs(self.folder, exist_ok=True)
                            with open(self._file(track_id), "w", encoding="utf-8") as f:
                                json.dump({"folder": folder, "points": [[round(x, 2), round(y, 2)] for x, y in pts]}, f)
                        except OSError:
                            pass
                        break
        finally:
            with self.lock:
                self.loops[track_id] = result
                self.busy.discard(track_id)


def to_world(loop, track_len):
    """Точки SVG (y вниз) -> метры (x вправо, z вверх), масштаб так, чтобы длина круга равнялась длине трассы."""
    total = _length(list(loop))
    k = track_len / total if total > 0 and track_len > 0 else 1.0
    return [(x * k, -y * k) for x, y in loop]
