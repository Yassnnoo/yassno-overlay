"""Рисованные руля для окна «Ввод»: сглаженные картинки (PIL, рисуем в 4 раза крупнее и уменьшаем), с объёмом и бликами.
Если PIL нет, оверлей и лаунчер рисуют руль старым способом (плоскими многоугольниками)."""
import math

try:
    from PIL import Image, ImageDraw
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


def _trace(cv, pts, color, depth=0.02):
    cv.d.line([cv.P(*point) for point in pts], fill=color,
              width=max(1, round(cv.r * depth)), joint="curve")


def _carbon(cv, bounds):
    x0, y0, x1, y1 = bounds
    row = 0
    y = y0
    while y < y1:
        x = x0
        while x < x1:
            color = ("#293039", "#252b33")[(row + round((x-x0)/.07)) % 2]
            _trace(cv, [(x, y), (min(x+.065, x1), min(y+.033, y1))], color, .016)
            x += .07
        y += .036
        row += 1


def _screw(cv, x, y, radius=.038):
    cv.disc(x, y, radius, "#0c1014", "#84919a", "#bcc7cb")
    _trace(cv, [(x-radius*.35, y), (x+radius*.35, y)], "#20262b", .012)


def _button(cv, x, y, color, radius=.071):
    cv.disc(x, y+.012, radius*1.18, "#080b0e", "#46525d")
    cv.disc(x, y, radius, "#101317", color, "#edf3fa")


def _dial(cv, x, y, color, radius=.12):
    cv.disc(x, y, radius, "#10151a", "#6e7c8c", "#a9b6c1")
    for index in range(12):
        a=index*30
        dx,dy=math.cos(math.radians(a)),math.sin(math.radians(a))
        _trace(cv, [(x+radius*.72*dx,y+radius*.72*dy),(x+radius*.9*dx,y+radius*.9*dy)], "#171c23", .012)
    cv.disc(x, y, radius*.62, "#1a1e24", "#454d58")
    _trace(cv, [(x, y-.012), (x+.018, y-radius*.76)], color, .024)


def _draw_formula(cv):
    cv.r *= 0.95  # хваты должны помещаться при любом угле поворота
    for s in (-1, 1):
        cv.poly([(s*x,y) for x,y in [(.51,-.42),(.87,-.49),(.94,-.31),(.9,.4),(.52,.44)]], "#34434e")
    face=[(-.73,-.56),(-.44,-.68),(.44,-.68),(.73,-.56),(.72,.39),(.47,.57),(-.47,.57),(-.72,.39)]
    for k,color in [(1,"#101519"),(.98,"#70808a"),(.947,"#232a32")]:
        cv.poly([(x*k,y*k) for x,y in face],color)
    _carbon(cv,(-.49,-.50,.49,.45))
    for s in (-1,1):
        for inset, color in [(0,"#0d1115"),(.035,"#3e4955"),(.07,"#232a31")]:
            path=[(.665+inset,-.49+inset),(.82,-.49+inset),(.875-inset,-.26),
                  (.90-inset,.28),(.85-inset,.66-inset),(.75,.73-inset),(.67+inset,.62),(.64+inset,.19)]
            cv.poly([(s*x,y) for x,y in path], color)
        _trace(cv,[(.78*s,-.36),(.82*s,-.1),(.82*s,.30),(.79*s,.56)],"#586571",.014)
        for j,col in enumerate(("#ed574f","#55a6ed","#e1b75d") if s<0 else ("#58cb92","#ed574f","#9eb2cc")):
            _button(cv,s*.55,-.27+j*.22,col)
        _screw(cv,s*.505,-.515)
        _screw(cv,s*.435,.465)
    cv.rrect(-.405,-.46,.405,.08,.04,"#080e14", "#66778a", max(1,cv.B//150))
    cv.rrect(-.365,-.42,.365,.035,.025,"#122534")
    # Цифра передачи на дисплее и два поля приборов.
    for seg in [[(-.07,-.35),(-.07,-.19),(.07,-.19)],[(.07,-.36),(.07,-.08)]]:
        _trace(cv,seg,"#e3f2f9",.034)
    for x in (-.305,.155):
        for j,length in enumerate((.14,.10,.13)):
            _trace(cv,[(x,-.31+j*.08),(x+length,-.31+j*.08)],"#66a8be",.022)
    for i in range(11):
        x=(i-5)*.074
        color="#66db81" if i<4 else "#f27255" if i<8 else "#60a6e4"
        cv.disc(x,-.55,.026,"#121820",color)
    _dial(cv,-.23,.28,"#ef735d")
    _dial(cv,.23,.28,"#7eaeee")


def _draw_gt(cv):
    for s in (-1,1):
        cv.poly([(s*x,y) for x,y in [(.43,-.46),(.7,-.5),(.83,-.31),(.81,.4),(.57,.43)]], "#313f49")
    cv.ring(.96,.765,"#161c24","#48515c","#75838e")
    cv.d.rectangle((*cv.P(-1,.70),*cv.P(1,1)),fill=(0,0,0,0))
    cv.capsule((-.62,.616),(.62,.616),.18,"#171e27","#45505b","#667582")
    for s in (-1,1):
        cv.capsule((s*.825,-.25),(s*.83,.245),.208,"#171e24","#3b454f","#63727d")
    face=[(-.75,-.075),(-.60,-.20),(-.28,-.16),(-.20,-.29),(.20,-.29),(.28,-.16),(.60,-.20),
          (.75,-.075),(.68,.22),(.30,.29),(.16,.62),(-.16,.62),(-.30,.29),(-.68,.22)]
    for k,col in [(1,"#11161c"),(.983,"#77848f"),(.943,"#28323b")]:
        cv.poly([(x*k,y*k) for x,y in face],col)
    _carbon(cv,(-.49,-.12,.49,.19))
    for s in (-1,1):
        _button(cv,s*.52,-.045,"#ee774b" if s<0 else "#5ca8f5")
        _button(cv,s*.52,.127,"#4f96d2" if s<0 else "#72cf9c",.059)
        _screw(cv,s*.64,.045,.028)
    cv.disc(0,.055,.225,"#171d26","#515f6a","#717f88")
    cv.disc(0,.055,.141,"#141a22","#2a343e","#42525d")
    _dial(cv,0,.37,"#e4bd6e",.092)
    cv.arc_mark(-7,7,.768,.95,"#ea644e")
    cv.arc_mark(-4,4,.905,.95,"#fd997c")


def _draw_rally(cv):
    for s in (-1,1):
        pts=[(s*x,y) for x,y in [(0,-.15),(.70,-.10),(.85,-.18),(.85,.085),(.68,.16),(0,.13)]]
        cv.poly(pts,"#252e36")
        _trace(cv,[pts[1],pts[2],pts[3]],"#b5bec0",.036)
        cv.capsule((s*.19,.016),(s*.68,.016),.13,"#46535f","#83949b","#bac2c2")
        for x,r in ((.39,.083),(.59,.061)):
            cv.hole(s*x,.009,r)
            _trace(cv,[(s*x-r*.62,.065),(s*x+r*.62,.065)],"#aab9bd",.014)
    cv.poly([(-.21,.10),(.21,.10),(.15,.83),(-.15,.83)],"#313b44")
    cv.capsule((0,.22),(0,.755),.21,"#46535d","#8e9c9e","#b9c7c8")
    for y,r in ((.40,.074),(.62,.056)):
        cv.hole(0,y,r)
    cv.disc(0,.015,.295,"#0f151b","#596873","#8c9ba3")
    cv.disc(0,.015,.164,"#171e23","#323e47","#4d5b63")
    for i in range(6):
        a = i * math.tau / 6
        _screw(cv,.22*math.cos(a),.015+.22*math.sin(a),.034)
    cv.ring(1,.76,"#121820","#51565a","#7b8180")
    for i in range(0,360,9):
        cv.arc_mark(i,i+2,.845,.889,"#596061")
    cv.arc_mark(-11,11,.76,.989,"#e6bb4f")
    cv.arc_mark(-7,8,.89,.98,"#f8d374")


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
