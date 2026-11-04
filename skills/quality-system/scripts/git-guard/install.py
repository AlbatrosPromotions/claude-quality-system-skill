#!/usr/bin/env python3
"""git-guard to'plamini loyihaga o'rnatadi (main ga faqat PR orqali kirish: 3 qatlam, README.md ga qarang).

  python3 install.py <loyiha-ildizi> [--baseline SHA] [--ref origin/main] [--protected main,master]
                     [--merge-style merge|squash|any] [--force] [--dry-run] [--skip-tests]

Yozadi: .claude/hooks/guard-git.py, .githooks/pre-push, .claude/quality/{main_guard.py,config.json,tests/test_git_guard.py},
.claude/settings.json (faqat qo'shadi: PreToolUse Bash va SessionStart yozuvlari, boshqasiga tegmaydi). Keyin shu klonda hook larni
yoqadi (main_guard.py --setup), o'rnatilgan testlarni va main_guard.py --verbose ni ishga tushiradi.
Fayl boshqacha bo'lsa --force siz ustiga yozmaydi. config.json bor bo'lsa uning qiymatlari saqlanadi (CLI optsiyasi ustun, o'zgarsa --force).
baseline standarti: --ref ning hozirgi SHA si, ya'ni "bundan keyin faqat PR merge". Faqat standart kutubxona.
"""
import argparse
import difflib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

KIT = Path(__file__).resolve().parent
FILES = [  # (kit dagi manba, loyihadagi yo'l, bajariladigan)
    ("guard-git.py", ".claude/hooks/guard-git.py", True),
    ("pre-push", ".githooks/pre-push", True),
    ("main_guard.py", ".claude/quality/main_guard.py", True),
    ("tests/test_git_guard.py", ".claude/quality/tests/test_git_guard.py", False),
]
CONFIG = ".claude/quality/config.json"
SETTINGS = ".claude/settings.json"
GUARD_CMD = '"$CLAUDE_PROJECT_DIR"/.claude/hooks/guard-git.py'
SESSION_CMD = '"$CLAUDE_PROJECT_DIR"/.claude/quality/main_guard.py --fetch --warn-only'
DEFAULT_PROTECTED = ["main", "master"]
MAX_DIFF = 30


class Fail(Exception):
    pass


def git(root, *args):
    r = subprocess.run(["git", "-C", str(root), *args], stdin=subprocess.DEVNULL, capture_output=True, text=True)
    return r.returncode, r.stdout.strip()


def has_command(groups, needle):
    return any(needle in str(h.get("command", "")) for g in groups if isinstance(g, dict)
               for h in (g.get("hooks") or []) if isinstance(h, dict))


def merge_settings(data):
    """settings.json ga ikki yozuv qo'shadi (bor bo'lsa qo'shmaydi). Qaytaradi: qo'shilgan qismlar ro'yxati; boshqa yozuvlarga tegmaydi."""
    if not isinstance(data, dict) or not isinstance(data.setdefault("hooks", {}), dict):
        raise Fail(f"{SETTINGS}: kutilmagan tuzilma (hooks obyekt emas)")
    added = []
    wanted = [
        ("PreToolUse", "guard-git.py", {"matcher": "Bash", "hooks": [
            {"type": "command", "command": GUARD_CMD, "timeout": 15, "onFailure": "block", "statusMessage": "main himoyasi"}]}),
        ("SessionStart", "main_guard.py", {"hooks": [{"type": "command", "command": SESSION_CMD, "timeout": 30}]}),
    ]
    for event, needle, group in wanted:
        groups = data["hooks"].setdefault(event, [])
        if not isinstance(groups, list):
            raise Fail(f"{SETTINGS}: hooks.{event} ro'yxat emas")
        if not has_command(groups, needle):
            groups.append(group)
            added.append(event)
    return added


def render(data):
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def show_diff(old, new, name):
    lines = list(difflib.unified_diff(old.splitlines(), new.splitlines(), f"loyihada/{name}", f"kit/{name}", lineterm="", n=1))
    for line in lines[:MAX_DIFF]:
        print("    " + line, file=sys.stderr)
    if len(lines) > MAX_DIFF:
        print(f"    ... yana {len(lines) - MAX_DIFF} qator", file=sys.stderr)


def resolve_config(root, args):
    """config.json mazmuni: CLI > mavjud config > standart; baseline standarti = ref ning hozirgi SHA si."""
    path = root / CONFIG
    existing = {}
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as e:
            raise Fail(f"{CONFIG} o'qilmadi: {e}")
        if not isinstance(existing, dict):
            raise Fail(f"{CONFIG}: JSON obyekt kutilgan")
    cfg = dict(existing)
    protected = [b.strip() for b in args.protected.split(",") if b.strip()] if args.protected else None
    if protected is not None and not protected:
        raise Fail("--protected bo'sh")
    cfg["protected"] = protected or cfg.get("protected") or list(DEFAULT_PROTECTED)
    cfg["ref"] = args.ref or cfg.get("ref") or "origin/" + cfg["protected"][0]
    base = args.baseline or cfg.get("baseline") or cfg["ref"]
    code, sha = git(root, "rev-parse", "--verify", "--quiet", base + "^{commit}")
    if code != 0:
        raise Fail(f"{base} topilmadi: `git fetch` qiling yoki --baseline SHA bering (--ref {cfg['ref']})")
    cfg["baseline"] = sha
    # merge_style: yangi config da doim yoziladi; mavjudida kalit yo'q = merge, faqat boshqacha so'ralsa qo'shiladi
    if args.merge_style and args.merge_style != existing.get("merge_style", "merge"):
        cfg["merge_style"] = args.merge_style
    elif not path.is_file():
        cfg["merge_style"] = "merge"
    keys = ("ref", "baseline", "protected", "merge_style")
    return {**{k: cfg[k] for k in keys if k in cfg}, **{k: v for k, v in cfg.items() if k not in keys}}, existing


def tail(text, n=15):
    return "\n".join(text.strip().splitlines()[-n:])


def run_step(title, cmd, root):
    t = time.time()
    r = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True)
    out = (r.stdout + r.stderr).strip("\n")
    if r.returncode != 0:
        print(f"  XATO {title} (kod {r.returncode}):\n{tail(out, 40)}", file=sys.stderr)
        return False
    summary = " | ".join(line for line in out.splitlines()[-3:] if line.strip()) if title.startswith("testlar") else ""
    print(f"  ok {title} ({time.time() - t:.1f} s)" + (f": {summary}" if summary else ""))
    if title.startswith("main_guard"):
        for line in out.splitlines():
            print("    " + line)
    return True


def install(args):
    root = Path(args.project).resolve()
    code, top = git(root, "rev-parse", "--show-toplevel")
    if code != 0 or Path(top).resolve() != root:
        raise Fail(f"{root} git repo ildizi emas" + (f" (repo ildizi: {top})" if top else ""))

    cfg, existing = resolve_config(root, args)
    cfg_text = render(cfg)

    # 1) rejalashtirish: hech narsa yozilmaydi
    plan, conflicts = [], []
    for src, dst, exe in FILES:
        new = (KIT / src).read_text(encoding="utf-8")
        target = root / dst
        if not target.exists():
            plan.append((target, new, exe, "yangi"))
            continue
        old = target.read_text(encoding="utf-8")
        if old == new:
            plan.append((target, None, exe, "o'zgarmagan"))
        else:
            plan.append((target, new, exe, "ustiga yoziladi (--force)"))
            conflicts.append((dst, old, new))
    cfg_path = root / CONFIG
    if not cfg_path.exists():
        plan.append((cfg_path, cfg_text, False, "yangi"))
    elif existing == cfg:
        plan.append((cfg_path, None, False, "o'zgarmagan"))
    else:
        plan.append((cfg_path, cfg_text, False, "ustiga yoziladi (--force)"))
        conflicts.append((CONFIG, json.dumps(existing, indent=2, ensure_ascii=False) + "\n", cfg_text))

    settings_path = root / SETTINGS
    settings = {}
    if settings_path.is_file():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except ValueError as e:
            raise Fail(f"{SETTINGS} o'qilmadi: {e}")
    added = merge_settings(settings)

    if conflicts and not args.force:
        print("Boshqacha mavjud fayllar bor, hech narsa yozilmadi. Farqlar:", file=sys.stderr)
        for name, old, new in conflicts:
            print(f"  {name}:", file=sys.stderr)
            show_diff(old, new, name)
        print("Ustiga yozish uchun --force bering.", file=sys.stderr)
        return 1

    prefix = "[dry-run] " if args.dry_run else ""
    for target, content, exe, state in plan:
        print(f"{prefix}{os.path.relpath(target, root)}: {state}")
    print(f"{prefix}{SETTINGS}: " + (", ".join(added) + " yozuvi qo'shiladi" if added else "o'zgarmagan (yozuvlar bor)"))
    print(f"{prefix}baseline = {cfg['baseline']} ({cfg['ref']}, himoyalangan: {','.join(cfg['protected'])}, merge_style: {cfg.get('merge_style', 'merge')})")
    if args.dry_run:
        return 0

    # 2) yozish
    for target, content, exe, _ in plan:
        if content is not None:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        if exe and target.exists():
            target.chmod(target.stat().st_mode | 0o111)
    if added:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(render(settings), encoding="utf-8")
    ignored = [dst for _, dst, _ in FILES if git(root, "check-ignore", "-q", dst)[0] == 0]
    if git(root, "check-ignore", "-q", CONFIG)[0] == 0:
        ignored.append(CONFIG)
    if ignored:
        print(f"OGOHLANTIRISH: .gitignore bu fayllarni yopadi, ular commit bo'lmaydi: {', '.join(ignored)}", file=sys.stderr)

    # 3) yoqish va tekshirish
    py = sys.executable
    ok = run_step("main_guard.py --setup --verbose", [py, ".claude/quality/main_guard.py", "--setup", "--verbose"], root)
    if not args.skip_tests:
        ok = run_step("testlar (.claude/quality/tests)", [py, "-m", "unittest", "discover", "-s", ".claude/quality/tests"], root) and ok
    if not ok:
        return 1

    print(f"""
Keyingi qadamlar:
  1. O'zgarishlarni feature branchda commit qiling va PR orqali main ga kiriting (main ga to'g'ridan-to'g'ri commit endi bloklanadi):
       git add .claude .githooks && git commit -m "git himoyasi"
  2. Loyiha darvozasiga (make ci / CI) qo'shing:
       python3 -B -m unittest discover -s .claude/quality/tests   (-B: __pycache__ ish daraxtiga yozilmasin, darvoza izi buzilmasin)
       python3 .claude/quality/main_guard.py        (CI da hook siz muhit uchun: --no-hooks-check)
  3. Har yangi klonda hook larni yoqing (masalan make git-hooks nishoni): python3 .claude/quality/main_guard.py --setup --verbose
  4. CLAUDE.md ga qoida qo'shing, masalan: "{cfg['protected'][0]} ga faqat PR va gh pr merge --merge orqali; .claude/hooks/guard-git.py,
     .githooks/pre-push va .claude/quality/main_guard.py ni chetlab o'tmang (blok = PR ochish)."
  5. GitHub dagi ruxsat etilgan merge usuli merge_style ({cfg.get('merge_style', 'merge')}) ga mos bo'lsin: merge = "Create a merge commit", squash = "Squash and merge"
     (qolgan usul main_guard.py ni qizil qiladi); main_guard.py committer noreply@github.com ni ham talab qiladi (GitHub serveri yaratgan commit).""")
    return 0


def main():
    ap = argparse.ArgumentParser(description="git-guard to'plamini loyihaga o'rnatadi")
    ap.add_argument("project", help="loyiha (git repo) ildizi")
    ap.add_argument("--baseline", help="shu commit dan keyin faqat PR merge (standart: --ref ning hozirgi SHA si)")
    ap.add_argument("--ref", help="tekshiriladigan ref (standart: mavjud config, yo'q bo'lsa origin/<birinchi himoyalangan branch>)")
    ap.add_argument("--protected", help="himoyalangan branchlar, vergul bilan (standart: main,master)")
    ap.add_argument("--merge-style", choices=("merge", "squash", "any"), help="GitHub merge usuli (standart: merge; mavjud config.json dagi saqlanadi)")
    ap.add_argument("--force", action="store_true", help="boshqacha mavjud fayllar ustiga yozadi")
    ap.add_argument("--dry-run", action="store_true", help="nima qilinishini ko'rsatadi, hech narsa yozmaydi")
    ap.add_argument("--skip-tests", action="store_true", help="o'rnatilgan testlarni ishga tushirmaydi (main_guard baribir ishlaydi)")
    try:
        return install(ap.parse_args())
    except Fail as e:
        print(f"xato: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
