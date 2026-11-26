"""rFactor 2: чтение и пересборка повторов (.vcr) — как rF2 Replay Office.

Формат (после gzip): заголовок (текст + сведения о сессии) | число машин |
записи машин | [срезов][событий][0.0][длительность] | срезы времени.
Срез = float время, u16 число событий, события подряд.
Событие = u32 заголовок (id машины | размер<<8 | тип<<17 | группа<<29), байт b, данные[размер].

Из повтора берутся круги выбранных пилотов (от пересечения линии до следующего
пересечения), каждый круг становится отдельной «машиной» нового повтора, все
круги стартуют одновременно в t=0.
"""
import gzip
import os
import struct

_F = struct.Struct("<f")
_MARK = b"\x01\x00\x00\x00\x0b\x00\x00\x00\x03\x00\x00\x00"   # три int перед числом машин


class VcrError(Exception):
    pass


def f32(x):
    """округление до float32 (как в C#)"""
    return _F.unpack(_F.pack(x))[0]


def read_vcr(path):
    with open(path, "rb") as f:
        data = f.read()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return data


def write_vcr(path, data, level=6):
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(gzip.compress(bytes(data), compresslevel=level, mtime=0))
    os.replace(tmp, path)


class Lap:
    """один круг пилота"""
    def __init__(self):
        self.driver = 0        # id машины в исходном повторе
        self.name = ""
        self.veh = ""
        self.num = 0           # номер круга (с 1)
        self.start = 0.0       # ET пересечения линии в начале круга
        self.end = 0.0         # ET пересечения линии в конце круга
        self.s1 = None         # накопленное время на S1
        self.s2 = None
        self.time = None       # время круга (None — круг не засчитан)

    @property
    def span(self):
        return self.end - self.start

    def __repr__(self):
        return "<Lap %s #%d %s>" % (self.name, self.num, self.time)


class Replay:
    def __init__(self, buf):
        self.buf = buf
        self._parse_header()
        self.slice_pos = []    # позиции срезов
        self.slice_t = []      # время срезов
        self._cp = {}          # id -> [(индекс среза, T, laptime, ET, lap, сектор, позиция события)]
        self._c216 = {}        # id -> [(индекс среза, позиция события)]
        self._scan()
        self.laps = self._build_laps()

    # ---------------------------------------------------------------- заголовок
    def _parse_header(self):
        b = self.buf
        if b[:2] != b"//":
            raise VcrError("это не повтор rFactor 2")
        nl = b.find(b"\n")
        self.version = _F.unpack_from(b, nl + 5)[0]
        if self.version < 1.08:
            raise VcrError("слишком старый формат повтора (%.2f)" % self.version)
        i = b.find(_MARK, 0x100, 0x40000)
        if i < 0:
            raise VcrError("не найдена таблица машин")
        self.count_pos = i + 12
        # байт «сессия», его Replay Office обнуляет
        j = b.rfind(b"\x00\x0a\xff\xff", 0, self.count_pos)
        self.flag_pos = j + 1 if j >= 0 else -1
        count = struct.unpack_from("<I", b, self.count_pos)[0]
        if count > 200:
            raise VcrError("странное число машин (%d)" % count)
        p = self.count_pos + 4
        self.drivers = []
        for _ in range(count):
            d = {}
            d["id"] = b[p]; p += 1
            for k in ("name", "co"):
                n = b[p]; p += 1
                d[k] = b[p:p + n].decode("latin1"); p += n
            for k in ("vehicle", "uid", "ver"):
                n = struct.unpack_from("<H", b, p)[0]; p += 2
                d[k] = b[p:p + n].decode("latin1"); p += n
            d["veh"] = b[p:p + 32].split(b"\0")[0].decode("latin1")
            p += 32
            d["raw"] = bytes(b[p:p + 56]); p += 56
            self.drivers.append(d)
        self.hdr_pos = p
        self.nslices, self.nevents, self.t0, self.duration = struct.unpack_from("<iiff", b, p)
        self.first = p + 16
        self.by_id = {d["id"]: d for d in self.drivers}

    # ------------------------------------------------------------------ срезы
    def _scan(self, progress=None):
        b = self.buf
        n = len(b)
        p = self.first
        unpack_ts = struct.Struct("<fH").unpack_from
        unpack_h = struct.Struct("<I").unpack_from
        pos_l = self.slice_pos
        t_l = self.slice_t
        cp = self._cp
        c216 = self._c216
        idx = 0
        total = self.nslices
        while idx < total and p + 6 <= n:
            t, cnt = unpack_ts(b, p)
            pos_l.append(p)
            t_l.append(t)
            q = p + 6
            for _ in range(cnt):
                h = unpack_h(b, q)[0]
                g = h >> 29
                if g == 3 and ((h >> 17) & 0x3f) == 6:
                    lt, et = struct.unpack_from("<ff", b, q + 5)
                    cp.setdefault(h & 255, []).append(
                        (idx, t, lt, et, b[q + 13], (b[q + 14] >> 6) & 3, q))
                elif g == 2 and ((h >> 17) & 0x3f) == 16:
                    c216.setdefault(h & 255, []).append((idx, q))
                q += 5 + ((h >> 8) & 0x1ff)
            p = q
            idx += 1
            if progress and not idx & 0x3fff:
                progress(idx / float(total))
        self.nslices_real = idx

    def _build_laps(self):
        laps = []
        for did, evs in self._cp.items():
            drv = self.by_id.get(did)
            if drv is None:
                continue
            prev = None
            s1 = s2 = None
            num = 0
            for (si, t, lt, et, lapb, sec, q) in evs:
                if sec == 0:
                    if prev is not None:
                        num += 1
                        L = Lap()
                        L.driver = did
                        L.name = drv["name"]
                        L.veh = drv["veh"]
                        L.num = num
                        L.start = prev
                        L.end = et
                        L.s1 = s1
                        L.s2 = s2
                        L.time = lt if lt > 0 else None
                        laps.append(L)
                    prev = et
                    s1 = s2 = None
                elif sec == 1:
                    s1 = lt if lt > 0 else None
                elif sec == 2:
                    s2 = lt if lt > 0 else None
        laps.sort(key=lambda L: (L.driver, L.start))
        return laps

    # ------------------------------------------------------------------ сборка
    def _events_of(self, si, did):
        """события машины did в срезе si: [(h, b, payload_pos, size)]"""
        b = self.buf
        p = self.slice_pos[si]
        cnt = struct.unpack_from("<H", b, p + 4)[0]
        q = p + 6
        out = []
        for _ in range(cnt):
            h = struct.unpack_from("<I", b, q)[0]
            size = (h >> 8) & 0x1ff
            if (h & 255) == did:
                out.append((h, b[q + 4], q + 5, size))
            q += 5 + size
        return out

    def _make_event(self, h, bb, ps, size, newid, shift=None):
        b = self.buf
        nh = (h - (h & 255) + newid) & 0xffffffff
        arr = bytearray(struct.pack("<I", nh))
        arr.append(bb)
        arr += b[ps:ps + size]
        if shift is not None:
            et = _F.unpack_from(arr, 9)[0]
            arr[9:13] = _F.pack(f32(et - shift))
        return bytes(arr)

    def _first_slice(self, lap):
        """индекс первого среза, где у машины есть позиция и T >= начала круга"""
        import bisect
        v0 = lap.start
        lo = bisect.bisect_left(self.slice_t, v0)
        # шаг назад на запас: T может быть чуть меньше ET
        did = lap.driver
        i = max(0, lo - 1)
        n = self.nslices_real
        while i < n:
            if self.slice_t[i] >= v0:
                for (h, bb, ps, size) in self._events_of(i, did):
                    g = h >> 29
                    sub = (h >> 17) & 0x3f
                    if g == 0 and 7 <= sub <= 15:
                        return i
            i += 1
        return n

    def build(self, picks, out_path=None, progress=None):
        """picks: список Lap (порядок = порядок новых машин). Возвращает bytes нового повтора."""
        if not picks:
            raise VcrError("не выбрано ни одного круга")
        if len(picks) > 100:
            raise VcrError("слишком много кругов (максимум 100)")
        b = self.buf
        n = len(picks)
        out = bytearray()
        out += b[:self.count_pos]
        if self.flag_pos >= 0:
            out[self.flag_pos] = 0
        out += struct.pack("<I", n)

        # ---- первая фаза: стартовые срезы и начальные события
        starts = []
        initial = []
        for i, lap in enumerate(picks):
            if progress:
                progress(0.05 + 0.15 * i / n, "Ищу начало круга: %s" % lap.name)
            si = self._first_slice(lap)
            starts.append(si)
            did = lap.driver
            v0 = f32(lap.start)
            ev216 = [e for e in self._c216.get(did, []) if e[0] <= si]
            if ev216:
                q = ev216[-1][1]
                h = struct.unpack_from("<I", b, q)[0]
                initial.append(self._make_event(h, b[q + 4], q + 5, (h >> 8) & 0x1ff, i))
            evcp = [e for e in self._cp.get(did, []) if e[0] <= si]
            if evcp and evcp[-1][0] < si:
                q = evcp[-1][6]
                h = struct.unpack_from("<I", b, q)[0]
                initial.append(self._make_event(h, b[q + 4], q + 5, (h >> 8) & 0x1ff, i, shift=v0))

        # событие «результаты»: порядок по времени круга
        def sort_key(k):
            t = picks[k].time
            return t if t is not None else 1e9
        order = sorted(range(n), key=sort_key)
        ranking = bytearray()
        size = 10 + n
        ranking += struct.pack("<I", (size << 8) + 0x60000000 + 0xA0000 + 255)
        ranking.append(0)
        ranking.append(n)
        ranking += bytes(9)
        for k in order:
            ranking.append(k)
        initial.append(bytes(ranking))

        # ---- вторая фаза: события кругов
        merged = {}      # время -> список событий
        for i, lap in enumerate(picks):
            if progress:
                progress(0.2 + 0.6 * i / n, "Собираю круг: %s" % lap.name)
            did = lap.driver
            v0 = f32(lap.start)
            s3at = lap.end
            si = starts[i]
            N = self.nslices_real
            while si < N:
                evs = self._events_of(si, did)
                T = self.slice_t[si]
                si += 1
                if not evs:
                    continue
                items = []
                for (h, bb, ps, size) in evs:
                    g = h >> 29
                    sub = (h >> 17) & 0x3f
                    if g == 0 and 7 <= sub <= 15:
                        items.append(self._make_event(h, bb, ps, size, i))
                    elif g == 3 and sub == 6:
                        items.append(self._make_event(h, bb, ps, size, i, shift=v0))
                    elif g == 2 and sub == 16:
                        items.append(self._make_event(h, bb, ps, size, i))
                tt = f32(T - v0)
                merged.setdefault(tt, []).extend(items)
                if T > s3at:
                    break

        times = sorted(merged)
        # как у Replay Office: в записи машин — самое долгое время круга,
        # в заголовке — оно же, округлённое вверх до шага 0.05 с
        raw_dur = 0.0
        for lap in picks:
            raw_dur = max(raw_dur, lap.time if lap.time else lap.span)
        raw_dur = f32(raw_dur)
        step = f32(0.05)
        k = int(raw_dur / step)
        if f32(k * step) < raw_dur:
            k += 1
        dur = f32(k * step)

        # ---- запись машин
        for i, lap in enumerate(picks):
            d = self.by_id[lap.driver]
            out.append(i)
            for k in ("name", "co"):
                s = d[k].encode("latin1")
                out.append(len(s)); out += s
            for k in ("vehicle", "uid", "ver"):
                s = d[k].encode("latin1")
                out += struct.pack("<H", len(s)); out += s
            out += d["veh"].encode("latin1")[:32].ljust(32, b"\0")
            raw = bytearray(d["raw"])
            raw[0x30:0x34] = struct.pack("<i", 0)
            raw[0x34:0x38] = _F.pack(raw_dur)
            out += raw

        hdr_pos = len(out)
        out += struct.pack("<iiff", 0, 0, 0.0, dur)

        def write_slice(t, events):
            out.extend(struct.pack("<fH", t, len(events)))
            for e in events:
                out.extend(e)

        write_slice(0.0, initial)
        ns = 1
        ne = len(initial)
        for k, t in enumerate(times):
            ev = merged[t]
            write_slice(t, ev)
            ns += 1
            ne += len(ev)
        struct.pack_into("<ii", out, hdr_pos, ns, ne)
        if progress:
            progress(0.9, "Сжимаю и сохраняю…")
        if out_path:
            write_vcr(out_path, out)
        if progress:
            progress(1.0, "Готово")
        return bytes(out)


def open_replay(path, progress=None):
    return Replay(read_vcr(path))
