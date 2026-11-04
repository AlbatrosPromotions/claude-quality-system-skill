#!/usr/bin/env python3
"""Darvoza izi: ishchi daraxt xeshi va `<prefix>-ci-ok` (loyihaning darvoza skripti yozadi, merge_guard.py o'qiydi).

  python3 .claude/quality/gate_stamp.py --tree                  # ishchi daraxt xeshini chiqaradi: kuzatilgan + yangi fayllar, .gitignore hisobga olinadi
  python3 .claude/quality/gate_stamp.py --write [--tree HASH]   # `<daraxt> <UTC vaqt>` izini yozadi; --tree bilan daraxt hozir boshqacha bo'lsa yozmaydi (1)
  python3 .claude/quality/gate_stamp.py --clear                 # izni o'chiradi (darvoza qizil)
  python3 .claude/quality/gate_stamp.py --check [--head SHA]    # iz daraxti HEAD (yoki SHA) daraxtiga tengmi: 0, aks holda 1 va sabab stderr ga
        [--repo PATH] [--config PATH]

Darvoza skriptida: boshida `tree=$(python3 .claude/quality/gate_stamp.py --tree)` (va eski yashil izni `--clear` bilan o'chirish), hammasi yashil
bo'lsa `python3 .claude/quality/gate_stamp.py --write --tree "$tree"`, qizil bo'lsa `--clear`. Yurish davomida fayllar o'zgarsa iz yozilmaydi.
Xesh: vaqtinchalik indeks nusxasida `git add -A`, so'ng `git write-tree`; haqiqiy indeksga tegilmaydi, vaqtinchalik fayl o'chiriladi. Nusxaning mtime i 1 s
(1970) ga qo'yiladi, shuning uchun git har yozuvni qayta xeshlaydi: stat mos kelgan, bir soniyada hajmi teng qayta yozilgan fayl ham o'tkazib yuborilmaydi
(narxi: har chaqiruvda hamma kuzatilgan fayl o'qiladi).
Iz: `git rev-parse --git-path <prefix>-ci-ok` (worktree va submodule da o'z git papkasi). Repo: --repo yoki joriy papkaniki.
Sozlama: `--config` > $QUALITY_CONFIG > <skript papkasi>/config.json; fayl yoki kalit yo'q = standart (prefix: quality), buzuq fayl = standart + ogohlantirish.
Chiqish kodi: 0 ok; 1 bajarilmadi (sabab stderr da); 2 noto'g'ri chaqiruv. Faqat standart kutubxona.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

DEFAULT_PREFIX = "quality"
PREFIX_OK = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
OID = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")  # sha1 yoki sha256
GIT_ENV_DROP = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_PREFIX", "GIT_COMMON_DIR")  # git hook ichidan chaqirilsa ham joriy repo olinsin
PRINT = object()  # yalang `--tree` (xeshni chiqarish); `--tree HASH` esa --write ning tekshiruvi


class Fail(Exception):
    """Amal bajarilmadi; xabar foydalanuvchiga (chiqish kodi 1)."""


def warn(text):
    print(f"ogohlantirish: {text}; standart sozlama ishlatiladi", file=sys.stderr)


def load_prefix(explicit=None):
    """--config > $QUALITY_CONFIG > <skript papkasi>/config.json dagi `prefix`; yo'q yoki yaroqsiz bo'lsa standart (buzuq fayl: bitta stderr qator)."""
    path = explicit or os.environ.get("QUALITY_CONFIG") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    data = {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as e:
        warn(f"{path} o'qilmadi ({e})")
    if not isinstance(data, dict):
        warn(f"{path}: JSON obyekt kutilgan")
        data = {}
    prefix = data.get("prefix")
    if prefix is None:
        return DEFAULT_PREFIX
    if not isinstance(prefix, str) or not PREFIX_OK.fullmatch(prefix):
        warn(f"{path}: `prefix` qiymati noto'g'ri")
        return DEFAULT_PREFIX
    return prefix


def git(repo, *args, env=None):
    env = env if env is not None else {k: v for k, v in os.environ.items() if k not in GIT_ENV_DROP}
    try:
        return subprocess.run(["git", "-C", repo, *args], stdin=subprocess.DEVNULL, capture_output=True, encoding="utf-8", errors="replace", env=env)
    except OSError as e:
        raise Fail(f"git ishga tushmadi: {e}")


def toplevel(repo):
    r = git(repo, "rev-parse", "--show-toplevel")
    top = r.stdout.strip()
    if r.returncode != 0 or not top:
        raise Fail(f"git repo emas: {repo}")
    return top


def git_path(top, name):
    """Iz fayli yo'li; git uni `top` ga nisbatan yoki mutlaq beradi."""
    r = git(top, "rev-parse", "--git-path", name)
    out = r.stdout.strip()
    if r.returncode != 0 or not out:
        raise Fail(f"git papkasi aniqlanmadi ({r.stderr.strip() or name})")
    return os.path.join(top, out)


def tree_hash(top):
    """Ishchi daraxt xeshi: haqiqiy indeksning nusxasida `git add -A` (shunda kuzatilgan lekin .gitignore dagi fayllar ham HEAD daraxtidagidek qoladi)."""
    real = git_path(top, "index")
    tmp = tempfile.mkdtemp(prefix="gate-stamp-")
    try:
        idx = os.path.join(tmp, "index")
        if os.path.isfile(real):
            shutil.copyfile(real, idx)
            # Nusxa yangi mtime oladi: git unga ishonib, stat (mtime, hajm) mos kelgan faylni qayta o'qimaydi. 1 s bo'lsa har yozuv "poygali toza" bo'lib qayta xeshlanadi;
            # 0 emas: git sec=0 ni "vaqt noma'lum" deb poyga tekshiruvini butunlay o'chiradi (o'lchangan).
            os.utime(idx, (1, 1))
        env = {k: v for k, v in os.environ.items() if k not in GIT_ENV_DROP}
        env["GIT_INDEX_FILE"] = idx
        add = git(top, "add", "-A", env=env)
        if add.returncode != 0:
            raise Fail("git add -A muvaffaqiyatsiz (" + (add.stderr.strip().splitlines() or ["sabab noma'lum"])[-1] + "): xesh ishonchsiz")
        out = git(top, "write-tree", env=env)
        tree = out.stdout.strip()
        if out.returncode != 0 or not OID.fullmatch(tree):
            raise Fail("git write-tree muvaffaqiyatsiz: " + (out.stderr.strip() or "chiqish bo'sh"))
        return tree
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def remove(path):
    try:
        os.remove(path)
        return True
    except FileNotFoundError:
        return False


def write_stamp(path, tree):
    """tmp + os.replace: o'quvchi yarim yozilgan izni ko'rmaydi."""
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(f"{tree} {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}\n")
        os.replace(tmp, path)
    finally:
        remove(tmp)


def do_write(top, prefix, expected):
    stamp = git_path(top, f"{prefix}-ci-ok")
    remove(stamp)  # hisoblash yiqilsa ham eski yashil natija qolmasin
    now = tree_hash(top)
    if expected is not None and not OID.fullmatch(expected):
        raise Fail(f"--tree qiymati daraxt xeshi emas ({expected!r}); eski iz o'chirildi")
    if expected is not None and expected != now:
        raise Fail(f"fayllar o'zgardi (boshida {expected}, hozir {now}): natija hisobga olinmaydi, eski iz o'chirildi; darvozani qayta ishga tushiring")
    write_stamp(stamp, now)
    print(f"darvoza izi yozildi: daraxt {now} ({stamp})")


def do_check(top, prefix, head):
    stamp = git_path(top, f"{prefix}-ci-ok")
    try:
        with open(stamp, encoding="utf-8", errors="replace") as f:
            fields = f.readline().split()
    except OSError:
        raise Fail(f"iz yo'q ({stamp}): darvoza yashil natijasi yozilmagan")
    if not fields or not OID.fullmatch(fields[0]):
        raise Fail(f"iz buzuq ({stamp}): birinchi maydon daraxt xeshi emas")
    ref = head or "HEAD"
    r = git(top, "rev-parse", "--verify", "--quiet", f"{ref}^{{tree}}")
    tree = r.stdout.strip()
    if r.returncode != 0 or not tree:
        raise Fail(f"{ref} daraxti aniqlanmadi")
    if fields[0] != tree:
        raise Fail(f"iz boshqa kod uchun (iz daraxti {fields[0]}, {ref} daraxti {tree}): commit qilinmagan o'zgarish bo'lishi mumkin; darvozani qayta ishga tushiring")
    print(f"darvoza izi {ref} daraxtiga mos: {tree}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="gate_stamp.py", allow_abbrev=False, description="Darvoza izi (<prefix>-ci-ok): ishchi daraxt xeshi.")
    ap.add_argument("--tree", nargs="?", const=PRINT, default=None, metavar="HASH", help="xeshni chiqaradi; --write bilan: yozishdan oldingi xesh")
    ap.add_argument("--write", action="store_true", help="izni yozadi")
    ap.add_argument("--clear", action="store_true", help="izni o'chiradi")
    ap.add_argument("--check", action="store_true", help="iz HEAD (yoki --head) daraxtiga mosligini tekshiradi")
    ap.add_argument("--head", metavar="SHA", help="--check uchun HEAD o'rniga")
    ap.add_argument("--repo", metavar="PATH", help="standart: joriy papka")
    ap.add_argument("--config", metavar="PATH", help="standart: $QUALITY_CONFIG, yo'q bo'lsa <skript papkasi>/config.json")
    a = ap.parse_args(argv)
    if a.tree is PRINT and a.write:
        ap.error("--write bilan --tree ga xesh kerak: --write --tree HASH")
    if a.tree is not None and a.tree is not PRINT and not a.write:
        ap.error("--tree HASH faqat --write bilan")
    modes = [name for name, on in (("--tree", a.tree is PRINT), ("--write", a.write), ("--clear", a.clear), ("--check", a.check)) if on]
    if len(modes) != 1:
        ap.error("aniq bitta amal kerak: --tree, --write, --clear yoki --check")
    if a.head is not None and not a.check:
        ap.error("--head faqat --check bilan")
    if a.head is not None and (not a.head or a.head.startswith("-")):
        ap.error("--head qiymati commit (SHA, branch) bo'lishi kerak")
    prefix = load_prefix(a.config)
    what = {"--tree": "daraxt xeshi hisoblanmadi", "--write": "darvoza izi yozilmadi", "--clear": "darvoza izi o'chirilmadi", "--check": "darvoza izi yaroqsiz"}[modes[0]]
    try:
        top = toplevel(a.repo or os.getcwd())
        if a.tree is PRINT:
            print(tree_hash(top))
        elif a.write:
            do_write(top, prefix, a.tree)
        elif a.clear:
            stamp = git_path(top, f"{prefix}-ci-ok")
            if remove(stamp):
                print(f"darvoza izi o'chirildi: {stamp}")
        else:
            do_check(top, prefix, a.head)
    except Fail as e:
        print(f"{what}: {e}", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"{what}: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
