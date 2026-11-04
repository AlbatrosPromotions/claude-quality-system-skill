#!/usr/bin/env python3
"""Spec testlari himoyasi: spec testlarni kodlovchi o'zgartirmaydi.

Spec testlar kodlovchidan oldin, faqat talab hujjatlaridan alohida agent (spec yozuvchi) tomonidan yoziladi; kodlovchi ularni yashil qiladi,
lekin o'zgartirmaydi. Spec yo'llari config `spec_paths` da (papka yoki fayl; ro'yxat bo'sh yoki yo'q = tekshiruv sozlanmagan, chiqish kodi 0).
Tekshiriladigan oraliq: `git merge-base <ref> HEAD`..HEAD.

  a) mavzusi `spec_prefix` (standart `spec:`, kichik harf, ikki nuqta bevosita) bilan boshlanadigan merge bo'lmagan commit faqat spec yo'llariga va
     `spec_extra` yo'llariga (masalan generatsiya qilinadigan matritsa) tegishi mumkin;
  b) boshqa har qanday merge bo'lmagan commit spec yo'llariga tegmaydi;
  c) merge commit birinchi ota-onasiga nisbatan o'zgartirgan har spec yo'li boshqa ota-onalardan birida xuddi shunday (blob yoki o'chirilgan)
     bo'lishi shart (aks holda "yovuz merge": spec o'zgarishi merge ichida yashiringan);
  d) ishchi daraxtda spec yo'llari ostida commit qilinmagan o'zgarish yo'q (staged, unstaged, kuzatilmagan): aks holda darvoza muhri
     keyin boshqa commit da yo'qqa chiqadigan spec o'zgarishini ham qoplab qo'yishi mumkin.

  python3 spec_guard.py [--repo PATH] [--ref origin/main] [--config PATH]

Yo'l yozuvlari (`spec_paths`, `spec_extra`) repo ildiziga nisbatan: aniq fayl yoki papka (oxiridagi "/" ixtiyoriy; `api/tests/Spec` va
`api/tests/SpecHelper.php` aralashmaydi). Sozlama: --config > $QUALITY_CONFIG > <skript papkasi>/config.json; ref: --ref > config `ref` > origin/<protected[0]>
(review_stamp.py bilan bir xil; `protected` yo'q yoki yaroqsiz bo'lsa origin/main).
Repo: --repo, standart skript papkasining git ildizi (yo'q bo'lsa joriy papka). Muammo bo'lsa o'zbekcha hisobot va chiqish kodi 1. Faqat standart kutubxona.
"""
import argparse
import json
import os
import posixpath
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_REF = "origin/main"
DEFAULT_PREFIX = "spec:"
DEFAULT_GATE = "make ci"
CHUNK = 200


class ConfigError(Exception):
    pass


def warn(message):
    print(f"spec_guard: {message}", file=sys.stderr)


def load_config(path_arg):
    """config.json mazmuni (dict): --config > $QUALITY_CONFIG > <skript papkasi>/config.json. Fayl yo'q = bo'sh, buzuq = bo'sh + stderr da bitta qator (qulamaydi)."""
    path = path_arg or os.environ.get("QUALITY_CONFIG") or os.path.join(SCRIPT_DIR, "config.json")
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        warn(f"{path} o'qilmadi ({exc}), standart sozlama ishlatiladi")
        return {}
    if not isinstance(data, dict):
        warn(f"{path}: JSON obyekt kutilgan, standart sozlama ishlatiladi")
        return {}
    return data


def string_value(cfg, key, default):
    value = cfg.get(key)
    if value is None:
        return default
    # bo'sh `spec_prefix` har commit mavzusiga mos kelib, (b) qoidasini o'chirib qo'yardi
    if isinstance(value, str) and value.strip():
        return value
    warn(f"config {key}: bo'sh bo'lmagan satr kutilgan, standart ({default}) ishlatiladi")
    return default


def default_ref(cfg):
    """origin/<protected[0]>: review_stamp.py ning asosiy branchi bilan bir xil. `protected` yo'q = origin/main; yaroqsiz bo'lsa ogohlantirish va origin/main."""
    value = cfg.get("protected")
    if value is None:
        return DEFAULT_REF
    if isinstance(value, list) and value and all(isinstance(item, str) and item for item in value):
        return "origin/" + value[0]
    warn(f"config protected: bo'sh bo'lmagan satrlar ro'yxati kutilgan, standart ({DEFAULT_REF}) ishlatiladi")
    return DEFAULT_REF


def normal_entry(entry):
    """Yo'l yozuvi -> repo ildiziga nisbatan normallashgan yo'l; ildizning o'zi, mutlaq yo'l, `..` va bo'sh yozuv None (hamma narsani spec qilib qo'yardi)."""
    if not isinstance(entry, str) or not entry.strip() or entry.startswith("/"):
        return None
    normal = posixpath.normpath(entry.strip().rstrip("/") or ".")
    return None if normal in (".", "..") or normal.startswith("../") else normal


def path_list(cfg, key):
    value = cfg.get(key)
    if value is None:
        return []
    entries = [normal_entry(item) for item in value] if isinstance(value, list) else [None]
    if any(item is None for item in entries):
        raise ConfigError(f"config {key} repo ildiziga nisbatan bo'sh bo'lmagan yo'llar ro'yxati bo'lishi kerak (ildiz, mutlaq yo'l va `..` mumkin emas)")
    return entries


def spec_settings(cfg):
    """{paths, prefix, extra}; noto'g'ri `spec_paths` / `spec_extra` ConfigError: tekshiruv jimgina o'chib qolmasin."""
    return {"paths": path_list(cfg, "spec_paths"), "prefix": string_value(cfg, "spec_prefix", DEFAULT_PREFIX), "extra": path_list(cfg, "spec_extra")}


def default_repo():
    """Skript papkasining git ildizi; git repo bo'lmasa (yoki git yo'q) joriy papka."""
    try:
        r = subprocess.run(["git", "-C", SCRIPT_DIR, "rev-parse", "--show-toplevel"], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
        top = r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        top = ""
    return os.path.abspath(top or os.getcwd())


def git(repo, *args, timeout=60):
    env = dict(os.environ, GIT_LITERAL_PATHSPECS="1")
    try:
        r = subprocess.run(
            ["git", "-C", repo, *args], stdin=subprocess.DEVNULL, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"git {args[0]} ishga tushmadi: {exc}")
    return r.returncode, r.stdout


def under(path, entries):
    return any(path == entry or path.startswith(entry + "/") for entry in entries)


def nul_list(out):
    return [p for p in out.split("\0") if p]


def commit_files(repo, sha):
    """Merge bo'lmagan commit o'zgartirgan yo'llar (nomi o'zgartirish ikki yo'l bo'ladi: eski va yangi)."""
    code, out = git(repo, "diff-tree", "-r", "--no-renames", "--root", "--no-commit-id", "--name-only", "-z", sha)
    if code != 0:
        raise RuntimeError(f"git diff-tree {sha[:7]} xato bilan tugadi")
    return nul_list(out)


def tree_entries(repo, rev, paths):
    """{yo'l: 'rejim sha'}; rev da bo'lmagan yo'l lug'atda yo'q (o'chirilgan)."""
    found = {}
    for i in range(0, len(paths), CHUNK):
        code, out = git(repo, "ls-tree", "-r", "-z", rev, "--", *paths[i:i + CHUNK])
        if code != 0:
            raise RuntimeError(f"git ls-tree {rev[:7]} xato bilan tugadi")
        for record in nul_list(out):
            meta, _, path = record.partition("\t")
            mode, _type, sha = meta.split(" ")
            found[path] = f"{mode} {sha}"
    return found


def check_plain(repo, spec, sha, subject):
    """Merge bo'lmagan commit: (qoida kaliti, yo'llar) yoki None."""
    files = commit_files(repo, sha)
    touched = [p for p in files if under(p, spec["paths"])]
    if subject.startswith(spec["prefix"]):
        other = [p for p in files if not under(p, spec["paths"]) and not under(p, spec["extra"])]
        return ("mixed", other) if other else None
    return ("plain", touched) if touched else None


def check_merge(repo, spec, sha, parents):
    """Merge commit: birinchi ota-onadan farq qilgan spec yo'llari, boshqa ota-onada o'sha ko'rinishda bo'lmaganlari."""
    code, out = git(repo, "diff-tree", "-r", "--no-renames", "--name-only", "-z", parents[0], sha)
    if code != 0:
        raise RuntimeError(f"git diff-tree {sha[:7]} xato bilan tugadi")
    changed = [p for p in nul_list(out) if under(p, spec["paths"])]
    if not changed:
        return None
    merged = tree_entries(repo, sha, changed)
    others = [tree_entries(repo, parent, changed) for parent in parents[1:]]
    bad = [p for p in changed if not any(merged.get(p) == entries.get(p) for entries in others)]
    return ("merge", bad) if bad else None


def check_worktree(repo, spec):
    code, out = git(repo, "status", "--porcelain=v1", "-z", "--no-renames", "--untracked-files=all", "--", *spec["paths"])
    if code != 0:
        raise RuntimeError("git status xato bilan tugadi")
    return sorted({record[3:] for record in nul_list(out)})


def rules(spec):
    prefix = spec["prefix"]
    extra = f" va {', '.join(spec['extra'])}" if spec["extra"] else ""
    return {
        "mixed": (
            f"`{prefix}` commit spec bo'lmagan yo'llarga ham tegdi",
            f"`{prefix}` commit faqat spec yo'llariga{extra} tegadi. Kod o'zgarishini alohida (`{prefix}` siz) commit qiling.",
        ),
        "plain": (
            f"`{prefix}` bo'lmagan commit spec yo'llariga tegdi",
            f"Spec testlarni kodlovchi o'zgartirmaydi: o'zgarishni qaytaring, yoki u spec ning o'zi bo'lsa alohida `{prefix} <mavzu>` commit qiling.",
        ),
        "merge": (
            "merge commit spec yo'lini ota-onalarining hech birida bo'lmagan ko'rinishga o'zgartirdi (yovuz merge)",
            f"Spec yo'lidagi ziddiyatni merge ichida o'zingiz hal qilmang: bir tomonni oling, kerakli o'zgarishni alohida `{prefix} <mavzu>` commit qiling.",
        ),
    }


def report(ref, base, violations, dirty, total, spec, gate):
    prefix = spec["prefix"]
    texts = rules(spec)
    extra = f" va {', '.join(spec['extra'])}" if spec["extra"] else ""
    lines = [f"spec_guard QIZIL: spec testlari qoidasi buzildi ({ref} bilan umumiy {base[:7]}..HEAD, {total} commit tekshirildi)"]
    for number, (sha, subject, key, paths) in enumerate(violations, 1):
        lines.append(f"\n{number}) {sha[:7]} {subject}")
        lines.append(f"   Qoida buzildi: {texts[key][0]}:")
        lines.extend(f"     - {p}" for p in paths)
    if dirty:
        lines.append("\nIshchi daraxtda commit qilinmagan spec o'zgarishlari (staged, unstaged yoki kuzatilmagan):")
        lines.extend(f"     - {p}" for p in dirty)
    lines.append(f"\nQoida: spec testlar ({', '.join(spec['paths'])}) kodlovchidan oldin faqat spec yozuvchi agent yozadi va faqat")
    lines.append(f"`{prefix}` bilan boshlanadigan commit orqali o'zgaradi; `{prefix}` commit spec yo'llaridan{extra} boshqa narsaga tegmaydi.")
    lines.append(f"Tuzatish: spec o'zgarishini alohida `{prefix} <mavzu>` commit qiling (kod commit lari spec yo'llariga tegmasin); commit qilinmagan spec")
    lines.append(f"o'zgarishlarini ham shunday commit qiling yoki qaytaring, so'ng `{gate}` ni qayta ishga tushiring.")
    seen = {key for _, _, key, _ in violations}
    for key in ("mixed", "plain", "merge"):
        if key in seen:
            lines.append(f"  - {texts[key][0]}: {texts[key][1]}")
    return "\n".join(lines)


def fetch_hint(ref):
    remote, sep, branch = ref.partition("/")
    return f"git fetch {remote} {branch}" if sep else "git fetch"


def run(repo, ref, spec, gate):
    """(chiqish kodi, matn)."""
    if git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")[0] != 0:
        return 1, f"spec_guard QIZIL: {ref} topilmadi, commit oralig'ini aniqlab bo'lmaydi: avval `{fetch_hint(ref)}`"
    code, out = git(repo, "merge-base", ref, "HEAD")
    base = out.strip()
    if code != 0 or not base:
        return 1, f"spec_guard QIZIL: {ref} va HEAD ning umumiy ajdodi topilmadi (shallow clone bo'lishi mumkin: `git fetch --unshallow`)"
    code, out = git(repo, "log", "-z", "--reverse", "--format=%H%x1f%P%x1f%s", f"{base}..HEAD")
    if code != 0:
        return 1, f"spec_guard QIZIL: git log {base[:7]}..HEAD xato bilan tugadi"
    violations = []
    records = nul_list(out)
    for record in records:
        sha, parents, subject = record.split("\x1f", 2)
        parents = parents.split()
        found = check_merge(repo, spec, sha, parents) if len(parents) > 1 else check_plain(repo, spec, sha, subject)
        if found:
            violations.append((sha, subject, found[0], found[1]))
    dirty = check_worktree(repo, spec)
    if violations or dirty:
        return 1, report(ref, base, violations, dirty, len(records), spec, gate)
    return 0, f"spec_guard: {len(records)} commit tekshirildi ({ref} bilan umumiy {base[:7]}..HEAD), spec qoidasi buzilmagan"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", help="git repo (standart: skript papkasining git ildizi, yo'q bo'lsa joriy papka)")
    ap.add_argument("--ref", help="taqqoslanadigan ref (standart: config ref, yo'q bo'lsa origin/<protected[0]>, u ham yo'q bo'lsa origin/main)")
    ap.add_argument("--config", help="config.json yo'li (standart: $QUALITY_CONFIG, keyin <skript papkasi>/config.json)")
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    try:
        spec = spec_settings(cfg)
    except ConfigError as exc:
        print(f"spec_guard QIZIL: {exc}", file=sys.stderr)
        return 1
    if not spec["paths"]:
        print("spec_guard: spec_paths sozlanmagan, tekshiruv yo'q")
        return 0
    repo = os.path.abspath(args.repo) if args.repo else default_repo()
    ref = args.ref or string_value(cfg, "ref", default_ref(cfg))
    gate = string_value(cfg, "gate_command", DEFAULT_GATE)
    try:
        code, text = run(repo, ref, spec, gate)
    except RuntimeError as exc:
        code, text = 1, f"spec_guard QIZIL: {exc}"
    print(text, file=sys.stderr if code else sys.stdout)
    return code


if __name__ == "__main__":
    sys.exit(main())
