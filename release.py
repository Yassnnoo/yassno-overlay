#!/usr/bin/env python3
"""Выпуск версии одной командой.

  python release.py 2.5.41 "Короткое описание" "- пункт README 1" "- пункт README 2"
      — поднимает версию в оверлее и лаунчере, обновляет version.json и README, компилирует,
        прогоняет тесты (tests/run_tests.py), копирует файлы в deploy/ и собирает zip.
  python release.py verify <папка_с_копией_с_ПК>
      — сверяет файлы, снятые с ПК, с теми, что должны там лежать (same / DIFF).
Пути настроены под рабочее окружение сессии; при переезде поправьте SRC / DEPLOY / PKG / OUT.
"""
import filecmp
import json
import os
import py_compile
import shutil
import subprocess
import sys

SRC = os.path.dirname(os.path.abspath(__file__))
PKG = "/tmp/y/yassno-overlay"                      # папка, из которой собирается zip
DEPLOY = "/mnt/user-data/outputs/deploy"           # то, что кладётся на ПК
OUT = "/mnt/user-data/outputs"
CODE = ["yassno_overlay.py", "yassno_launcher.py", "yassno_svgmap.py", "yassno_readers.py", "README.txt", "version.json"]


def fail(msg):
    print("ОШИБКА:", msg)
    sys.exit(1)


def verify(folder):
    bad = 0
    for name in CODE:
        a, b = os.path.join(folder, name), os.path.join(DEPLOY, name)
        if not os.path.isfile(a):
            print("нет на ПК:", name)
            bad += 1
        elif not filecmp.cmp(a, b, shallow=False):
            print("DIFF", name, os.path.getsize(a), "≠", os.path.getsize(b))
            bad += 1
        else:
            print("same", name)
    sys.exit(1 if bad else 0)


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "verify":
        verify(sys.argv[2])
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(2)
    ver, notes, items = sys.argv[1], sys.argv[2], sys.argv[3:]
    old = None
    for f in ("yassno_overlay.py", "yassno_launcher.py"):
        p = os.path.join(SRC, f)
        s = open(p, encoding="utf-8").read()
        import re
        m = re.search(r'APP_VERSION = "([\d.]+)"', s)
        if not m:
            fail("нет APP_VERSION в " + f)
        old = m.group(1)
        open(p, "w", encoding="utf-8").write(s.replace(m.group(0), f'APP_VERSION = "{ver}"', 1))
    vp = os.path.join(SRC, "version.json")
    v = json.load(open(vp, encoding="utf-8"))
    v["version"], v["notes"] = ver, notes
    for need in ("yassno_readers.py", "yassno_svgmap.py", "logos/tile_ac_on.png", "logos/tile_ac_off.png", "logos/peugeot.png", "logos/genesis.png", "logos/ginetta.png", "logos/ligier.png") + tuple("logos/c/%s.png" % n for n in ("ferrari","porsche","bmw","mclaren","astonmartin","chevrolet","lamborghini","ford","lexus","mercedes","toyota","peugeot","cadillac","alpine","genesis","ginetta","ligier")):
        if need not in v["files"]:
            v["files"].append(need)
    v["files"] = sorted(set(v["files"]))
    json.dump(v, open(vp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    rp = os.path.join(SRC, "README.txt")
    r = open(rp, encoding="utf-8").read()
    marker = f"=== {old} ==="
    if marker not in r:
        fail(f"в README нет блока {marker}")
    block = f"=== {ver} ===\n" + "\n".join(items or [notes]) + "\n\n"
    open(rp, "w", encoding="utf-8").write(r.replace(marker, block + marker, 1))
    for f in CODE:
        if f.endswith(".py"):
            try:
                py_compile.compile(os.path.join(SRC, f), doraise=True)
            except py_compile.PyCompileError as e:
                fail(f"{f} не компилируется: {e}")
    t = subprocess.run([sys.executable, os.path.join(SRC, "tests", "run_tests.py")], cwd=SRC, capture_output=True, text=True)
    tail = (t.stderr or t.stdout).strip().splitlines()[-3:]
    print("тесты:", " | ".join(tail))
    if t.returncode != 0:
        fail("тесты не прошли — выпуск остановлен (версия уже поднята в файлах, откатите или исправьте)")
    for dst in (PKG, DEPLOY):
        os.makedirs(dst, exist_ok=True)
        for f in CODE:
            shutil.copyfile(os.path.join(SRC, f), os.path.join(dst, f))
    if os.path.isdir(os.path.join(SRC, "plugins")):
        for dst in (PKG, DEPLOY):
            shutil.copytree(os.path.join(SRC, "plugins"), os.path.join(dst, "plugins"), dirs_exist_ok=True)
    if os.path.isdir(os.path.join(SRC, "logos")):
        shutil.copytree(os.path.join(SRC, "logos"), os.path.join(PKG, "logos"), dirs_exist_ok=True)
    shutil.copytree(os.path.join(SRC, "tests"), os.path.join(PKG, "tests"), dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copyfile(os.path.join(SRC, "release.py"), os.path.join(PKG, "release.py"))
    zp = os.path.join(OUT, f"yassno-overlay-{ver}.zip")
    if os.path.exists(zp):
        os.remove(zp)
    subprocess.run(["zip", "-qr", zp, os.path.basename(PKG)], cwd=os.path.dirname(PKG), check=True)
    print(f"готово: {old} → {ver}, {zp} ({os.path.getsize(zp)} байт)")
    for f in CODE:
        print(f"  {f}: {os.path.getsize(os.path.join(DEPLOY, f))} байт")


if __name__ == "__main__":
    main()
