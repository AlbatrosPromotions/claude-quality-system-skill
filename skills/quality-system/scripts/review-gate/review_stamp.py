#!/usr/bin/env python3
"""Mustaqil ko'rik izi: ko'rikchi agent PR ning aniq head commitini ko'rib chiqqach yozadi.

  python3 .claude/quality/review_stamp.py --pr N --head SHA --report FILE   ko'rik oxirida: hisobot PR ga izoh, keyin iz
  python3 .claude/quality/review_stamp.py --pr N --check                    ko'rikdan oldin: navbatdagi tur chegaraga sig'adimi (hech narsa yozmaydi)
  python3 .claude/quality/review_stamp.py --pr N --allow [--round K]        foydalanuvchi "ha" degach lead: K-tur (standart 3) ruxsati

Joriy papkadagi git repo bilan ishlaydi (loyiha repo si yoki submodule: shu papkaga `cd` qilib chaqiring).
Sozlama: `--config` > $QUALITY_CONFIG > <skript papkasi>/config.json; fayl yoki kalit yo'q = standart, buzuq fayl = standart + ogohlantirish.
  prefix (standart quality): iz nomlari <prefix>-review-ok-<PR>, <prefix>-review-allow va PR izohidagi `<!-- <prefix>-review ... -->` belgisi;
  protected[0] (main) = base branch; ref (origin/<base>); gate_command (make ci); source_labels (["Source"]); sensitive (regex ro'yxati).
  1. gh pr view N: PR ochiq, base = base branch, headRefOid == SHA; SHA daraxti lokal repoda bor.
  2. Ko'rik turlari chegarasi: tur raqami = PR izohlaridagi ko'rik belgilari soni + 1. 2-tur faqat 1-turda bloker yoki muhim bo'lsa;
     3-tur va keyingilari faqat ruxsat izi bilan: `git rev-parse --git-path <prefix>-review-allow` = `<PR> <tur> <UTC ISO>`
     (`--allow`, foydalanuvchi "ha" degach lead yozadi); iz turning izohi yozilgach o'chadi (bir ruxsat = bir tur).
     Chegaradan tashqari chaqiruv hech narsa yozmaydi, chiqish kodi 4.
  3. FILE (ko'rikchi hisoboti, markdown): oxirgi bo'sh bo'lmagan qator
       VERDICT: blockers=<n> major=<n> minor=<n>[ security=<done|n/a>]      (security bo'lmasa n/a)
  4. blockers > 0: eski iz o'chadi, hisobot PR ga izoh bo'lib yoziladi, chiqish kodi 3.
  5. PR matnida `<source_labels dan biri>: <matn>` qatori bor (HTML izohlar `<!-- -->` hisobga olinmaydi).
  6. O'zgargan fayllardan biri `sensitive` regexlaridan biriga mos kelsa security=done shart.
  7. Hisobot + belgi bilan `gh pr comment`, keyin iz: `git rev-parse --git-path <prefix>-review-ok-<PR>`
     = `<daraxt> <PR> <SHA> <UTC ISO> round=<k> blockers=<n> major=<n> minor=<n>` (merge hooki tekshiradi:
     1-tur izi faqat major=0 bilan merge ga yaroqli, keyingi commitlar uchun iz head i joriy head ning ajdodi bo'lsa yetarli).
     Iz fayli PR bo'yicha: boshqa PR ning ko'rigi (toza yoki blokerli) bu PR izini o'chirmaydi va ustidan yozmaydi.
Izni faqat shu skript yozadi; uni faqat ko'rikchi agent chaqira oladi (merge hooki). Faqat standart kutubxona.
Chiqish kodlari: 0 iz yozildi (--check: tur ruxsat etilgan; --allow: ruxsat yozildi); 1 rad etildi (xato); 2 noto'g'ri argument; 3 blokerlar bor; 4 ko'rik turlari chegarasi.
"""
import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

DEFAULTS = {
    "prefix": "quality",
    "gate_command": "make ci",
    "source_labels": ["Source"],
    "sensitive": [r"^\.claude/", r"^\.githooks/", r"^\.github/workflows/"],
    "protected": ["main", "master"],
    "ref": None,  # None = origin/<base>
}
# kalit -> qiymat yaroqliligi; yaroqsiz qiymat standartga qaytadi (None = kalit yo'q)
VALID = {
    "prefix": lambda v: isinstance(v, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", v) is not None,
    "gate_command": lambda v: isinstance(v, str) and v.strip() != "",
    "source_labels": lambda v: isinstance(v, list) and len(v) > 0 and all(isinstance(x, str) and x.strip() for x in v),
    "sensitive": lambda v: isinstance(v, list) and all(isinstance(x, str) for x in v),
    "protected": lambda v: isinstance(v, list) and len(v) > 0 and all(isinstance(x, str) and x for x in v),
    "ref": lambda v: isinstance(v, str) and v != "",
}
VERDICT = re.compile(r"^VERDICT: blockers=([0-9]+) major=([0-9]+) minor=([0-9]+)(?: security=(done|n/a))?$")
SHA = re.compile(r"[0-9a-f]{40}")
HTML_COMMENT = re.compile(r"<!--.*?(?:-->|\Z)", re.S)  # PR shablonidagi `Source: <!-- majburiy ... -->` to'ldirilmagan qator hisoblanmaydi


class Refused(Exception):
    """Iz yozilmaydi; xabar foydalanuvchiga (chiqish kodi 1)."""


class Limited(Refused):
    """Ko'rik turlari chegarasi: bu tur o'tkazilmaydi, hech narsa yozilmaydi (chiqish kodi 4)."""


class Cfg(object):
    """config.json qiymatlari va ulardan hosil bo'lgan nomlar."""

    def __init__(self, raw):
        self.prefix = raw["prefix"]
        self.gate = raw["gate_command"]
        self.labels = raw["source_labels"]
        self.sensitive = raw["sensitive"]
        self.base = raw["protected"][0]
        self.ref = raw["ref"] or "origin/" + self.base
        self.stamp_name = self.prefix + "-review-ok"
        self.allow_name = self.prefix + "-review-allow"
        # PR izohidagi tur belgisi; `round=` eski belgilarda yo'q (ular ham tur)
        self.marker_re = re.compile(
            r"<!-- " + re.escape(self.prefix) + r"-review head=[0-9a-f]+ tree=[0-9a-f]+ blockers=([0-9]+) major=([0-9]+) minor=([0-9]+)"
            r" security=(?:done|n/a)(?: round=[0-9]+)? -->")
        alt = "|".join(re.escape(label.strip()) for label in self.labels)
        self.source_re = re.compile(
            r"^[ \t]*(?:[-*>][ \t]+)?(?:\*\*|__)?(?:" + alt + r")(?:\*\*|__)?[ \t]*:(?:\*\*|__)?[ \t]*(\S.*)$", re.M | re.I)


def load_config(explicit=None):
    """--config > $QUALITY_CONFIG > <skript papkasi>/config.json. Fayl yoki kalit yo'q = standart; buzuq fayl = standart + bitta stderr qator."""
    path = explicit or os.environ.get("QUALITY_CONFIG") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    data, problems = {}, []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        pass
    except (OSError, ValueError, RecursionError) as e:  # RecursionError: haddan tashqari chuqur JSON
        problems.append(f"{path} o'qilmadi ({e})")
    if not isinstance(data, dict):
        problems.append(f"{path}: JSON obyekt kutilgan")
        data = {}
    raw = dict(DEFAULTS)
    for key, ok in VALID.items():
        if data.get(key) is None:
            continue
        if ok(data[key]):
            raw[key] = data[key]
        else:
            problems.append(f"{path}: `{key}` qiymati noto'g'ri")
    for p in problems:
        print(f"ogohlantirish: {p}; standart sozlama ishlatiladi", file=sys.stderr)
    return Cfg(raw)


def run(cmd, **kw):
    try:
        return subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, errors="replace", **kw)
    except OSError as e:
        return subprocess.CompletedProcess(cmd, 127, "", str(e))


def git(*args):
    """git chiqishi (strip); xato bo'lsa None."""
    r = run(["git", *args])
    return r.stdout.strip() if r.returncode == 0 else None


def gh(*args):
    """Yagona kirish nuqtasi: hamma gh chaqiruvlari shu yerdan (testlarda PATH dagi soxta gh)."""
    return run(["gh", *args])


def err(r):
    return r.stderr.strip() or "noma'lum xato"


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_verdict(report):
    lines = [ln.strip() for ln in report.splitlines() if ln.strip()]
    m = VERDICT.match(lines[-1]) if lines else None
    if not m:
        raise Refused("hisobotning oxirgi bo'sh bo'lmagan qatori `VERDICT: blockers=<n> major=<n> minor=<n>` (ixtiyoriy ` security=<done|n/a>`) bo'lishi shart")
    return int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4) or "n/a"


def load_pr(cfg, number, head=None):
    r = gh("pr", "view", str(number), "--json", "number,state,headRefOid,body,baseRefName,comments")
    if r.returncode != 0:
        raise Refused(f"gh pr view {number} bajarilmadi: {err(r)}")
    try:
        pr = json.loads(r.stdout)
    except ValueError:
        raise Refused(f"gh pr view {number} javobi JSON emas")
    if not isinstance(pr, dict):
        raise Refused(f"gh pr view {number} javobi JSON obyekt emas")
    if pr.get("state") != "OPEN":
        raise Refused(f"PR #{number} ochiq emas (status: {pr.get('state')})")
    if pr.get("baseRefName") != cfg.base:
        raise Refused(f"PR #{number} base i {cfg.base} emas ({pr.get('baseRefName')})")
    if head is not None and str(pr.get("headRefOid") or "").lower() != head:
        raise Refused(f"PR #{number} head i {pr.get('headRefOid')}, so'ralgan {head}: PR o'zgargan, oxirgi commit ni ko'rib chiqing")
    if not isinstance(pr.get("comments"), list):
        raise Refused(f"gh pr view {number} javobida `comments` ro'yxati yo'q: ko'rik turlarini sanab bo'lmaydi")
    return pr


def rounds(cfg, pr):
    """O'tgan turlar PR izohlari tartibida: [(blockers, major, minor)]; bir izohda bir belgi; matnsiz yozuv tur emas."""
    bodies = [c.get("body") for c in pr["comments"] if isinstance(c, dict)]
    return [tuple(int(x) for x in m.groups()) for b in bodies if isinstance(b, str) for m in [cfg.marker_re.search(b)] if m]


def git_path(name):
    p = git("rev-parse", "--git-path", name)
    if not p:
        raise Refused("bu papka git repo emas")
    return p


def write_atomic(path, text):
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except OSError as e:
        raise Refused(f"{path} yozib bo'lmadi: {e}")


def read_allow(path):
    """Ruxsat izi `<PR> <tur> <vaqt>` -> (PR, tur); yo'q yoki buzuq bo'lsa None."""
    try:
        with open(path, encoding="utf-8") as f:
            fields = f.read().split()
    except (OSError, ValueError):
        return None
    if len(fields) < 2 or not re.fullmatch(r"[0-9]+", fields[0]) or not re.fullmatch(r"[0-9]+", fields[1]):
        return None
    return int(fields[0]), int(fields[1])


def allow_command(number, k):
    script = os.path.abspath(__file__)
    try:
        script = os.path.relpath(script)
    except ValueError:
        pass
    return f"python3 {shlex.quote(script)} --pr {number} --allow --round {k}"


def next_round(cfg, pr, number, allow):
    """Navbatdagi tur raqami yoki Limited: 2-tur faqat 1-turda bloker yoki muhim bo'lsa, 3-tur va keyingilari faqat ruxsat izi bilan."""
    done = rounds(cfg, pr)
    k = len(done) + 1
    if k == 2 and done[0][0] == 0 and done[0][1] == 0:
        raise Limited(f"PR #{number} 1-ko'rigi toza (bloker 0, muhim 0): qayta ko'rik yo'q, keyingi commitlar faqat `{cfg.gate}` bilan, keyin merge")
    if k >= 3 and read_allow(allow) != (number, k):
        raise Limited(f"PR #{number} uchun {k}-ko'rik faqat foydalanuvchi ruxsati bilan: foydalanuvchiga savol, \"ha\" bo'lsa "
                      f"`{allow_command(number, k)}` (submodule PR i bo'lsa o'sha papkadan), keyin ko'rik; o'tgan turlar: "
                      + ", ".join(f"{i + 1}-tur bloker {b} muhim {m} mayda {n}" for i, (b, m, n) in enumerate(done)))
    return k


def consume_allow(path, k):
    """Ruxsat bir turga: izohi yozilgan tur ruxsatni ishlatadi."""
    if k >= 3:
        try:
            os.unlink(path)
        except OSError:
            pass


def sensitive_regexes(cfg):
    out = []
    for i, pattern in enumerate(cfg.sensitive):
        try:
            out.append(re.compile(pattern))
        except re.error as e:  # jim o'tkazib yuborish himoyani olib tashlardi
            raise Refused(f"config.json: sensitive[{i}] regex noto'g'ri ({pattern!r}): {e}")
    return out


def sensitive_paths(cfg, head):
    regexes = sensitive_regexes(cfg)
    base = git("merge-base", cfg.ref, head)
    if not base:
        remote, _, branch = cfg.ref.partition("/")
        fetch = f"git fetch {remote} {branch}" if branch else f"git fetch origin {cfg.ref}"
        raise Refused(f"{cfg.ref} bilan umumiy ota topilmadi: `{fetch}`, keyin qayta urinib ko'ring")
    r = run(["git", "diff", "--name-only", "--no-renames", "-z", base, head])
    if r.returncode != 0:
        raise Refused(f"git diff {base[:7]}..{head[:7]} xato bilan tugadi")
    return [p for p in r.stdout.split("\0") if p and any(rx.search(p) for rx in regexes)]


def post_comment(number, report, marker):
    body = report.rstrip() + "\n\n" + marker + "\n"
    with tempfile.NamedTemporaryFile("w", suffix=".md", encoding="utf-8", delete=False) as f:
        f.write(body)
        path = f.name
    try:
        r = gh("pr", "comment", str(number), "--body-file", path)
    finally:
        os.unlink(path)
    if r.returncode != 0:
        raise Refused(f"gh pr comment bajarilmadi: {err(r)}; iz yozilmadi")


def source_line_missing(cfg, number):
    names = " yoki ".join(f"`{label.strip()}: <matn>`" for label in cfg.labels)
    return Refused(f"PR #{number} matnida {names} qatori yo'q (manba izi qoidasi): PR matniga manbani yozing")


def write_allow(cfg, number, k):
    try:
        path = git_path(cfg.allow_name)
        write_atomic(path, f"{number} {k} {now()}\n")
    except Refused as e:
        print(f"ruxsat izi yozilmadi: {e}", file=sys.stderr)
        return 1
    print(f"ruxsat izi yozildi: PR #{number}, {k}-tur ({os.path.abspath(path)}); ko'rikdan keyin o'chadi")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="mustaqil ko'rik izini yozadi (faqat ko'rikchi agent); --allow: foydalanuvchi ruxsati",
                                 allow_abbrev=False)  # qisqartirilgan --rep/--he shakllari merge hookining matn tekshiruvidan o'tib ketmasin
    ap.add_argument("--pr", required=True, type=int, help="PR raqami")
    ap.add_argument("--head", help="ko'rib chiqilgan head commit (to'liq 40 belgili SHA)")
    ap.add_argument("--report", help="ko'rikchi hisoboti (markdown), oxirgi qator VERDICT: ...")
    ap.add_argument("--check", action="store_true", help="faqat ko'rik turlari chegarasini tekshirish, hech narsa yozmaydi")
    ap.add_argument("--allow", action="store_true", help="foydalanuvchi ruxsati: K-ko'rik (standart 3) uchun ruxsat izini yozadi")
    ap.add_argument("--round", type=int, help="--allow bilan: ruxsat etiladigan tur (3 yoki katta, standart 3)")
    ap.add_argument("--config", help="standart: $QUALITY_CONFIG, yo'q bo'lsa <skript papkasi>/config.json")
    a = ap.parse_args(argv)
    if a.pr < 1:
        ap.error("--pr musbat butun son bo'lishi shart")
    k = 3 if a.round is None else a.round
    if a.allow:
        if a.check or a.head or a.report:
            ap.error("--allow ni --check, --head, --report bilan birga ishlatib bo'lmaydi")
        if k < 3:
            ap.error("--round 3 yoki undan katta bo'lishi shart: 1- va 2-ko'rik ruxsatsiz ochiladi")
    elif a.round is not None:
        ap.error("--round faqat --allow bilan ishlatiladi")
    elif not a.check and (not a.head or not a.report):
        ap.error("--head va --report kerak (yoki --check, --allow)")
    cfg = load_config(a.config)
    if a.allow:
        return write_allow(cfg, a.pr, k)
    try:
        if a.check:
            allow = git_path(cfg.allow_name)
            k = next_round(cfg, load_pr(cfg, a.pr), a.pr, allow)
            print(f"PR #{a.pr}: {k}-ko'rik ruxsat etilgan" + (f" (ruxsat izi {os.path.abspath(allow)})" if k >= 3 else ""))
            return 0
        head = a.head.lower()
        if not SHA.fullmatch(head):
            raise Refused("--head to'liq 40 belgili commit SHA si bo'lishi shart")
        try:
            with open(a.report, encoding="utf-8") as f:
                report = f.read()
        except (OSError, ValueError) as e:
            raise Refused(f"hisobot o'qilmadi: {e}")
        blockers, major, minor, security = parse_verdict(report)
        stamp = git_path(f"{cfg.stamp_name}-{a.pr}")
        allow = git_path(cfg.allow_name)
        pr = load_pr(cfg, a.pr, head)
        k = next_round(cfg, pr, a.pr, allow)
        tree = git("rev-parse", "--verify", "--quiet", f"{head}^{{tree}}")
        if not tree:
            raise Refused(f"commit {head[:7]} lokal repoda yo'q: `git fetch`, keyin qayta urinib ko'ring")
        marker = (f"<!-- {cfg.prefix}-review head={head} tree={tree} blockers={blockers} major={major} minor={minor} "
                  f"security={security} round={k} -->")

        if blockers > 0:  # blokerli hisobot izni hech qachon yozmaydi; shu PR ning eski izi (shu daraxt uchun ham) o'chadi
            try:
                os.unlink(stamp)
            except FileNotFoundError:
                pass
            except OSError as e:  # eski iz qolsa merge hooki uni yaroqli deb olardi
                raise Refused(f"eski iz o'chirilmadi ({stamp}): {e}")
            post_comment(a.pr, report, marker)
            consume_allow(allow, k)
            print(f"blokerlar bor: iz yozilmadi (blockers={blockers}, {k}-tur); hisobot PR #{a.pr} ga izoh qilindi", file=sys.stderr)
            return 3

        if not cfg.source_re.search(HTML_COMMENT.sub("", (pr.get("body") or "").replace("\r", ""))):
            raise source_line_missing(cfg, a.pr)
        touched = sensitive_paths(cfg, head)
        if touched and security != "done":
            raise Refused("sezgir fayllar o'zgargan, hisobotda security=done (xavfsizlik ko'rigi) bo'lishi shart:\n"
                          + "\n".join(f"    {p}" for p in touched[:20]) + (f"\n    ... va yana {len(touched) - 20} ta" if len(touched) > 20 else ""))

        post_comment(a.pr, report, marker)
        consume_allow(allow, k)
        write_atomic(stamp, f"{tree} {a.pr} {head} {now()} round={k} blockers={blockers} major={major} minor={minor}\n")
        note = "; 1-turda muhim bor: merge uchun tuzatish va 2-ko'rik shart" if k == 1 and major > 0 else ""
        print(f"ko'rik izi yozildi: PR #{a.pr}, head {head[:7]}, daraxt {tree[:7]}, {k}-tur ({os.path.abspath(stamp)}){note}")
        return 0
    except Limited as e:
        print(f"ko'rik turlari chegarasi: {e}", file=sys.stderr)
        return 4
    except Refused as e:
        print(f"ko'rik izi yozilmadi: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
