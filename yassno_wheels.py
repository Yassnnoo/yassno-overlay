"""Рисованные руля для окна «Ввод»: сглаженные картинки (PIL, рисуем в 4 раза крупнее и уменьшаем), с объёмом и бликами.
Если PIL нет, оверлей и лаунчер рисуют руль старым способом (плоскими многоугольниками)."""
import math

try:
    from PIL import Image, ImageDraw, ImageFilter
    OK = True
except Exception:                                   # pragma: no cover
    OK = False

SS = 4                                              # во сколько раз рисуем крупнее
STYLES = ("round", "formula", "gt", "rally")
_base_cache = {}


def _hex(c):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def _lerp(a, b, t):
    a, b = (_hex(a) if isinstance(a, str) else a), (_hex(b) if isinstance(b, str) else b)
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _profile(t, edge, mid, hi=None):
    """Окраска поперёк трубки: t = 0..1 от края к краю — тёмные края, светлая середина (и блик чуть выше середины)."""
    k = 1.0 - abs(t - 0.42) / 0.58 if t > 0.42 else 1.0 - abs(t - 0.42) / 0.42
    k = max(0.0, k) ** 0.8
    col = _lerp(edge, mid, k)
    if hi:
        h = max(0.0, 1.0 - abs(t - 0.36) / 0.12) ** 2
        col = _lerp(col, hi, h * 0.55)
    return col + (255,)


class _Canvas:
    def __init__(self, B):
        self.B = B
        self.im = Image.new("RGBA", (B, B), (0, 0, 0, 0))
        self.d = ImageDraw.Draw(self.im)
        self.c = B / 2.0
        self.r = B / 2.0 * 0.95

    def P(self, x, y):
        return (self.c + x * self.r, self.c + y * self.r)

    def ring(self, ro, ri, edge, mid, hi=None, steps=None):
        """Кольцо с поперечным объёмом от радиуса ro до ri (доли радиуса)."""
        R0, R1 = ro * self.r, ri * self.r
        n = steps or max(8, int(R0 - R1))
        for i in range(n):
            t = i / max(1, n - 1)
            rad = R0 + (R1 - R0) * t
            w = max(1, int(abs(R0 - R1) / n) + 2)
            self.d.ellipse((self.c - rad, self.c - rad, self.c + rad, self.c + rad), outline=_profile(t, edge, mid, hi), width=w)

    def capsule(self, p1, p2, width, edge, mid, hi=None):
        """Толстая линия со скруглёнными концами и объёмом (p1, p2 в долях радиуса, width — в долях радиуса)."""
        (x1, y1), (x2, y2) = self.P(*p1), self.P(*p2)
        W = width * self.r
        n = max(6, int(W / 2))
        for i in range(n):
            t = i / max(1, n - 1)
            w = W * (1 - t * 0.0) * (1.0 - 0.0)
            ww = W - (W * 0.0)
            ww = W * (1.0 - (abs(t - 0.42) * 0))      # ширина фиксирована, меняется только цвет — ниже слои уже
        # слои от широкого к узкому: края тёмные, середина светлая
        layers = 14
        for i in range(layers):
            t = i / (layers - 1)
            w = W * (1.0 - t * 0.78)
            col = _profile(0.5 - t * 0.5 if t < 0.8 else 0.42, edge, mid, hi) if False else _lerp(edge, mid, min(1.0, t * 1.15)) + (255,)
            if hi and t > 0.8:
                col = _lerp(mid, hi, (t - 0.8) / 0.2 * 0.6) + (255,)
            self.d.line((x1, y1, x2, y2), fill=col, width=max(1, int(w)))
            rr = w / 2.0
            for (x, y) in ((x1, y1), (x2, y2)):
                self.d.ellipse((x - rr, y - rr, x + rr, y + rr), fill=col)

    def disc(self, x, y, rad, edge, mid, hi=None, layers=14):
        cx, cy = self.P(x, y)
        R = rad * self.r
        for i in range(layers):
            t = i / (layers - 1)
            rr = R * (1.0 - t * 0.85)
            col = _lerp(edge, mid, min(1.0, t * 1.1)) + (255,)
            if hi and t > 0.75:
                col = _lerp(mid, hi, (t - 0.75) / 0.25 * 0.7) + (255,)
            self.d.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), fill=col)

    def hole(self, x, y, rad):
        """Отверстие: делаем область прозрачной."""
        cx, cy = self.P(x, y)
        R = rad * self.r
        self.d.ellipse((cx - R, cy - R, cx + R, cy + R), fill=(0, 0, 0, 0))

    def poly(self, pts, fill):
        self.d.polygon([self.P(*p) for p in pts], fill=fill)

    def rrect(self, x0, y0, x1, y1, rad, fill, outline=None, width=1):
        (a, b), (c, d) = self.P(x0, y0), self.P(x1, y1)
        self.d.rounded_rectangle((a, b, c, d), radius=rad * self.r, fill=fill, outline=outline, width=width)

    def arc_mark(self, a0, a1, r0, r1, fill):
        """Цветная метка на ободе: сектор между углами a0..a1 (градусы от верха по часовой) и радиусами r0..r1."""
        pts = []
        for a in [a0 + (a1 - a0) * i / 12 for i in range(13)]:
            pts.append((math.sin(math.radians(a)) * r1, -math.cos(math.radians(a)) * r1))
        for a in [a1 + (a0 - a1) * i / 12 for i in range(13)]:
            pts.append((math.sin(math.radians(a)) * r0, -math.cos(math.radians(a)) * r0))
        self.poly(pts, fill)


LEATHER = ("#0f1114", "#3a3e46", "#5a606b")
METAL = ("#2f323a", "#a9aeb8", "#e9ecf1")
CARBON = ("#0b0c0f", "#23262c", "#3a3f49")
RED = (229, 50, 45, 255)
GOLD = (255, 179, 0, 255)


def _stitch(cv, rad, n, col, length=0.05):
    """Прошивка кожи вдоль обода: короткие штрихи по окружности."""
    for i in range(n):
        a = 2 * math.pi * i / n
        x0, y0 = math.sin(a) * (rad - length / 2), -math.cos(a) * (rad - length / 2)
        x1, y1 = math.sin(a) * (rad + length / 2), -math.cos(a) * (rad + length / 2)
        cv.d.line((*cv.P(x0, y0), *cv.P(x1, y1)), fill=col, width=max(1, int(cv.B / 220)))


def _draw_round(cv):
    cv.ring(1.0, 0.80, *LEATHER)
    _stitch(cv, 0.90, 90, (205, 210, 220, 190), 0.0)
    # три спицы: влево, вправо, вниз
    for ang in (-90, 90, 180):
        ex, ey = math.sin(math.radians(ang)) * 0.84, -math.cos(math.radians(ang)) * 0.84
        cv.capsule((0, 0), (ex, ey), 0.17, *METAL)
    cv.disc(0, 0, 0.30, "#23262c", "#8a8f99", "#dfe3ea")
    cv.disc(0, 0, 0.19, "#15171b", "#3b3f47", "#6a707b")
    cv.disc(0, 0, 0.07, "#8a8f99", "#e9ecf1")
    cv.arc_mark(-7, 7, 0.80, 1.0, RED)                                  # красная метка «12 часов»


def _draw_gt(cv):
    # обод со срезом снизу: маска «кольцо минус всё ниже линии среза»
    B = cv.B
    ring = Image.new("RGBA", (B, B), (0, 0, 0, 0))
    saved = cv.im, cv.d
    cv.im, cv.d = ring, ImageDraw.Draw(ring)
    cv.ring(1.0, 0.80, *LEATHER)
    cut = cv.c + 0.70 * cv.r
    ImageDraw.Draw(ring).rectangle((0, cut, B, B), fill=(0, 0, 0, 0))
    cv.im, cv.d = saved
    cv.im.alpha_composite(ring)
    cv.d = ImageDraw.Draw(cv.im)
    for i in range(10):                                                 # плоский низ обода: хорда со скруглёнными краями и объёмом
        t = i / 9.0
        col = _lerp("#0f1114", "#3a3e46", min(1.0, t * 1.2)) + (255,)
        cv.rrect(-0.73 + t * 0.10, 0.54 + t * 0.045, 0.73 - t * 0.10, 0.705 - t * 0.045, 0.06, fill=col)
    cv.rrect(-0.55, 0.585, 0.55, 0.60, 0.01, fill=(110, 116, 128, 130))
    _stitch(cv, 0.90, 70, (205, 210, 220, 160), 0.0)
    # две боковые спицы-«плечи» и нижняя
    cv.capsule((-0.84, 0.02), (0, 0.04), 0.20, *METAL)
    cv.capsule((0.84, 0.02), (0, 0.04), 0.20, *METAL)
    cv.capsule((0, 0.04), (0, 0.58), 0.18, *METAL)
    cv.disc(0, 0.04, 0.30, "#23262c", "#8a8f99", "#dfe3ea")
    cv.disc(0, 0.04, 0.19, "#15171b", "#3b3f47", "#6a707b")
    cv.disc(0, 0.04, 0.07, "#8a8f99", "#e9ecf1")
    cv.arc_mark(-7, 7, 0.80, 1.0, RED)


def _draw_rally(cv):
    cv.ring(1.0, 0.72, "#08090b", "#2d3037", "#555b66")                 # толстый обод
    _stitch(cv, 0.86, 70, (170, 176, 188, 150), 0.0)
    for ang in (-90, 90, 180):
        ex, ey = math.sin(math.radians(ang)) * 0.78, -math.cos(math.radians(ang)) * 0.78
        cv.capsule((0, 0), (ex, ey), 0.26, *METAL)
        mx, my = ex * 0.62, ey * 0.62
        cv.hole(mx, my, 0.07)                                           # облегчающие отверстия
    cv.disc(0, 0, 0.34, "#1b1d22", "#7b808a", "#d3d7de")
    cv.disc(0, 0, 0.20, "#0d0e11", "#262930", "#4b505a")
    for px, py in ((-0.27, -0.27), (0.27, -0.27), (-0.27, 0.27), (0.27, 0.27)):
        cv.disc(px, py, 0.04, "#454a54", "#cdd1d8")
    cv.arc_mark(-9, 9, 0.72, 1.0, GOLD)                                 # жёлтая лента сверху, как на раллийных рулях


def _draw_formula(cv):
    # корпус формульного руля: прямоугольник со срезанными верхними углами и «хватами» по бокам
    def body(inset, fill):
        k = 1.0 - inset
        pts = [(-0.80, -0.62), (-0.45, -0.74), (0.45, -0.74), (0.80, -0.62), (0.96, -0.30), (1.00, 0.30), (0.97, 0.78),
               (0.86, 0.90), (0.74, 0.82), (0.70, 0.52), (0.52, 0.46), (-0.52, 0.46), (-0.70, 0.52), (-0.74, 0.82),
               (-0.86, 0.90), (-0.97, 0.78), (-1.00, 0.30), (-0.96, -0.30)]
        cv.poly([(x * k, y * k) for x, y in pts], fill)
    body(0.0, (8, 9, 11, 255))
    body(0.03, (34, 37, 43, 255))
    body(0.07, (22, 24, 28, 255))
    # прорезиненные хваты
    for sx in (-1, 1):
        cv.rrect(0.80 * sx - 0.12 if sx > 0 else -0.98, -0.18, 0.98 if sx > 0 else -0.80 + 0.12, 0.70, 0.07, fill=(46, 50, 58, 255))
    # дисплей
    cv.rrect(-0.46, -0.56, 0.46, -0.06, 0.05, fill=(4, 5, 7, 255), outline=(70, 76, 88, 255), width=max(2, cv.B // 150))
    for i in range(15):                                                 # светодиоды шифт-лайта над дисплеем
        x = -0.40 + 0.80 * i / 14
        col = (46, 226, 109, 255) if i < 5 else (255, 59, 48, 255) if i < 10 else (74, 144, 255, 255)
        cv.disc(x, -0.66, 0.030, tuple(int(v * 0.5) for v in col[:3]), col[:3])
    for i, (w, col) in enumerate(((0.6, (46, 226, 109)), (0.35, (255, 193, 7)), (0.8, (74, 163, 255)))):   # строки данных на дисплее
        y = -0.46 + i * 0.13
        cv.rrect(-0.38, y, -0.38 + 0.76 * w, y + 0.06, 0.02, fill=col + (255,))
    # кнопки и поворотные переключатели
    cols = ((230, 60, 55), (255, 193, 7), (60, 200, 110), (74, 144, 255))
    for i, (px, py) in enumerate(((-0.68, -0.30), (-0.68, 0.02), (0.68, -0.30), (0.68, 0.02))):
        cv.disc(px, py, 0.095, (20, 22, 26), cols[i])
    for px in (-0.30, 0.30):
        cv.disc(px, 0.24, 0.12, "#1a1c20", "#9aa0ab", "#e4e7ec")
        cv.d.line((*cv.P(px, 0.24), *cv.P(px, 0.15)), fill=(20, 22, 26, 255), width=max(2, cv.B // 120))
    cv.rrect(-0.06, -0.72, 0.06, -0.62, 0.02, fill=GOLD)                # жёлтая метка «12 часов»


_DRAW = {"round": _draw_round, "formula": _draw_formula, "gt": _draw_gt, "rally": _draw_rally}


def base_image(style, size):
    """Руль в нулевом положении, в SS раз крупнее size."""
    key = (style, size)
    im = _base_cache.get(key)
    if im is None:
        cv = _Canvas(size * SS)
        _DRAW.get(style, _draw_round)(cv)
        im = cv.im
        if len(_base_cache) > 12:
            _base_cache.clear()
        _base_cache[key] = im
    return im


def render(style, size, angle):
    """Готовая картинка руля size×size, повёрнутая на angle градусов по часовой стрелке."""
    im = base_image(style, size)
    if abs(angle) > 0.3:
        im = im.rotate(-angle, resample=Image.BICUBIC)
    return im.resize((size, size), Image.LANCZOS)
