"""Маршрут по дорогам ETS2: граф дорог (растр z6, ets2_roads.txt) + A*. Без numpy."""
import array, base64, heapq, json, math, os, struct, threading, unicodedata, zlib

S = 512 * 64                      # размер карты z6 в пикселях
X1, Y1, XW, YH = -94621.8047, -80209.1641, 79370.13 + 94621.8047, 93782.77 + 80209.1641
M_PER_PX = XW / S                 # игровых метров в пикселе z6
CELL = 24


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return "".join(ch for ch in s if ch.isalnum())


def world_to_px(gx, gz):
    return (gx - X1) / XW * S, (gz - Y1) / YH * S


class RoadGraph:
    def __init__(self, here):
        with open(os.path.join(here, "ets2_roads.txt"), "rb") as f:
            raw = zlib.decompress(base64.b64decode(f.read()))
        if raw[:4] != b"YRG1":
            raise ValueError("bad graph")
        n, m = struct.unpack("<II", raw[4:12])
        o = 12
        self.n = n
        self.x = array.array("H"); self.x.frombytes(raw[o:o + 2 * n]); o += 2 * n
        self.y = array.array("H"); self.y.frombytes(raw[o:o + 2 * n]); o += 2 * n
        self.w = raw[o:o + n]; o += n
        ea = array.array("I"); ea.frombytes(raw[o:o + 4 * m]); o += 4 * m
        eb = array.array("I"); eb.frombytes(raw[o:o + 4 * m]); o += 4 * m
        fl = raw[o:o + m]
        adj = [[] for _ in range(n)]
        x, y, w = self.x, self.y, self.w
        for i in range(m):
            a, b, f = ea[i], eb[i], fl[i]
            d = math.hypot(x[a] - x[b], y[a] - y[b])
            # шире дорога — быстрее; перемычки дешёвые, паромы/тоннели дорогие
            ww = (w[a] + w[b]) / 16.0
            c = d * (1.7 - 0.12 * min(ww, 7.0))
            if f == 2:
                c = d * 1.6 + 60
            adj[a].append((b, c, d)); adj[b].append((a, c, d))
        self.adj = adj
        self.grid = {}
        for i in range(n):
            self.grid.setdefault((x[i] // CELL, y[i] // CELL), []).append(i)
        with open(os.path.join(here, "ets2_cities.txt"), encoding="utf-8") as f:
            self.cities = json.load(f)

    def nearest(self, px, py, rmax=400):
        cx, cy = int(px // CELL), int(py // CELL)
        best, bd = -1, 1e18
        r = 0
        while r * CELL <= rmax:
            for gx in range(cx - r, cx + r + 1):
                for gy in range(cy - r, cy + r + 1):
                    if max(abs(gx - cx), abs(gy - cy)) != r:
                        continue
                    for i in self.grid.get((gx, gy), ()):
                        d = (self.x[i] - px) ** 2 + (self.y[i] - py) ** 2
                        if d < bd:
                            best, bd = i, d
            if best >= 0 and math.sqrt(bd) <= r * CELL:
                break
            r += 1
        return best

    def city_px(self, name):
        return self.cities.get(norm(name))

    def route(self, a, b):
        """A* от узла a к b. Возвращает (список узлов, длина в пикселях) или None."""
        x, y, adj = self.x, self.y, self.adj
        tx, ty = x[b], y[b]
        h = lambda i: math.hypot(x[i] - tx, y[i] - ty) * 0.55          # допустимая оценка (мин. коэф. ~0.86)
        g = {a: 0.0}; prev = {}; pq = [(h(a), a)]; done = set()
        while pq:
            _, u = heapq.heappop(pq)
            if u in done:
                continue
            if u == b:
                path = [u]
                while u in prev:
                    u = prev[u]; path.append(u)
                path.reverse()
                ln = sum(math.hypot(x[p] - x[q], y[p] - y[q]) for p, q in zip(path, path[1:]))
                return path, ln
            done.add(u)
            gu = g[u]
            for v, c, _d in adj[u]:
                ng = gu + c
                if ng < g.get(v, 1e18):
                    g[v] = ng; prev[v] = u
                    heapq.heappush(pq, (ng + h(v), v))
        return None

    def route_px(self, p0, p1):
        a, b = self.nearest(*p0), self.nearest(*p1)
        if a < 0 or b < 0:
            return None
        r = self.route(a, b)
        if not r:
            return None
        path, ln = r
        pts = [(self.x[i], self.y[i]) for i in path]
        return {"pts": [p0] + pts + [p1], "km": ln * M_PER_PX / 1000.0}
