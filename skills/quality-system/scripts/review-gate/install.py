#!/usr/bin/env python3
"""review-gate to'plamini loyihaga o'rnatadi (mustaqil ko'rik izi, merge qo'riqchisi, ko'rik turlari chegarasi, darvoza izi: README.md ga qarang).

  python3 install.py <loyiha-ildizi> [--prefix quality] [--reviewer opus-reviewer] [--gate-command "make ci"] [--source-labels Source,Ref]
                     [--sensitive REGEX]... [--submodules a,b] [--spec-paths a/,b/] [--spec-extra PATH]...
                     [--force] [--dry-run] [--skip-tests]

Oldin git-guard o'rnatilgan bo'lishi shart (.claude/quality/config.json va .claude/hooks/guard-git.py). Yozadi: .claude/quality/{review_stamp,gate_stamp,
quality_log,spec_guard}.py, .claude/hooks/{merge-guard,review-rounds}.py, .claude/quality/tests/ (test_install.py dan tashqari hamma test),
.claude/quality/.gitignore (`__pycache__/`: darvozada testlar yurganda CPython yozgan bayt-kod daraxt xeshini o'zgartirmasin),
.claude/agents/<reviewer>.md (reviewer.md shabloni), .claude/quality/config.json (mavjud kalitlar saqlanadi: CLI > mavjud > standart) va
.claude/settings.json (faqat qo'shadi: ikki PreToolUse yozuvi, boshqasiga tegmaydi). Keyin o'rnatilgan testlarni ishga tushiradi (~5 daqiqa).
Fayl boshqacha bo'lsa --force siz ustiga yozmaydi; config.json dagi mavjud qiymatni CLI boshqacha qilsa ham --force kerak. Faqat .claude/quality/.gitignore bundan
mustasno: foydalanuvchi qatorlari saqlanadi, `__pycache__/` yo'q bo'lsa oxiriga qo'shiladi (ziddiyat ham, --force ham kerak emas). Faqat standart kutubxona.
"""
import argparse
import copy
import difflib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

KIT = Path(__file__).resolve().parent
GIT_GUARD = KIT.parent / "git-guard" / "install.py"
FILES = [  # (kit dagi manba, loyihadagi yo'l, bajariladigan)
    ("review_stamp.py", ".claude/quality/review_stamp.py", True),
    ("gate_stamp.py", ".claude/quality/gate_stamp.py", True),
    ("quality_log.py", ".claude/quality/quality_log.py", True),
    ("spec_guard.py", ".claude/quality/spec_guard.py", True),
    ("merge_guard.py", ".claude/hooks/merge-guard.py", True),
    ("review_rounds.py", ".claude/hooks/review-rounds.py", True),
]
TESTS_DST = ".claude/quality/tests"
GITIGNORE = ".claude/quality/.gitignore"
GITIGNORE_TEXT = "__pycache__/\n"
GITIGNORE_HAS = {"__pycache__/", "__pycache__", "**/__pycache__/", "**/__pycache__"}  # tests/__pycache__ ni ham yopadigan yozuvlar (`/__pycache__/` yopmaydi)
TEMPLATE = "reviewer.md"
AGENTS = ".claude/agents"
CONFIG = ".claude/quality/config.json"
SETTINGS = ".claude/settings.json"
GUARD_HOOK = ".claude/hooks/guard-git.py"
MERGE_CMD = '"$CLAUDE_PROJECT_DIR"/.claude/hooks/merge-guard.py'
ROUNDS_CMD = '"$CLAUDE_PROJECT_DIR"/.claude/hooks/review-rounds.py'
HOOKS = [  # (hodisa, yozuv bor-yo'qligini bilish uchun buyruq bo'lagi, guruh)
    ("PreToolUse", "merge-guard.py", {"matcher": "Bash", "hooks": [
        {"type": "command", "command": MERGE_CMD, "timeout": 30, "onFailure": "block", "statusMessage": "merge darvozasi"}]}),
    ("PreToolUse", "review-rounds.py", {"matcher": "Agent", "hooks": [
        {"type": "command", "command": ROUNDS_CMD, "timeout": 30, "onFailure": "block", "statusMessage": "ko'rik turlari chegarasi"}]}),
]
DEFAULTS = {  # config.json ning yangi kalitlari; skriptlar ham shu standartlarni biladi
    "prefix": "quality",
    "gate_command": "make ci",
    "reviewer": "opus-reviewer",
    "source_labels": ["Source"],
    "sensitive": [r"^\.claude/", r"^\.githooks/", r"^\.github/workflows/"],
    "submodules": [],
    "spec_paths": [],
    "spec_prefix": "spec:",
    "spec_extra": [],
    "quality_log": {
        "file": "docs/quality-log.md",
        "system_prefixes": ["ci", "docs", "test", "review", "quality", "agent", "style", "arch", "git", "build", "spec", "dev", "config", "dup", "seed"],
        "minor_rule_from": None,
        "gate_script": None,
    },
}
FLAGS = {"prefix": "--prefix", "gate_command": "--gate-command", "reviewer": "--reviewer", "source_labels": "--source-labels", "sensitive": "--sensitive",
         "submodules": "--submodules", "spec_paths": "--spec-paths", "spec_extra": "--spec-extra"}
PREFIX_OK = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")  # iz fayl nomlari va PR izohi belgisi uchun (skriptlardagi qoida bilan bir xil)
AGENT_OK = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")  # agent nomi va fayl nomi: yo'l belgisi va nuqta yo'q
PLACEHOLDER = re.compile(r"\{([^{}]*)\}")
MAX_DIFF = 30
TESTS_TIMEOUT = 1800


class Fail(Exception):
    pass


def git(root, *args):
    r = subprocess.run(["git", "-C", str(root), *args], stdin=subprocess.DEVNULL, capture_output=True, text=True)
    return r.returncode, r.stdout.strip()


def has_command(groups, needle):
    return any(needle in str(h.get("command", "")) for g in groups if isinstance(g, dict)
               for h in (g.get("hooks") or []) if isinstance(h, dict))


def merge_settings(data):
    """settings.json ga ikki PreToolUse yozuvi qo'shadi (bor bo'lsa qo'shmaydi). Qaytaradi: qo'shilganlar ro'yxati; boshqa yozuvlarga tegmaydi."""
    if not isinstance(data, dict) or not isinstance(data.setdefault("hooks", {}), dict):
        raise Fail(f"{SETTINGS}: kutilmagan tuzilma (hooks obyekt emas)")
    added = []
    for event, needle, group in HOOKS:
        groups = data["hooks"].setdefault(event, [])
        if not isinstance(groups, list):
            raise Fail(f"{SETTINGS}: hooks.{event} ro'yxat emas")
        if not has_command(groups, needle):
            groups.append(copy.deepcopy(group))
            added.append(needle)
    return added


def render(data):
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def render_agent(template, values):
    """Shablondagi {reviewer}, {source_label}, {gate_command} ni bir o'tishda almashtiradi (format emas: qiymatdagi { } buzmasin)."""
    unknown = sorted(set(PLACEHOLDER.findall(template)) - set(values))
    if unknown:
        raise Fail(f"{TEMPLATE}: noma'lum o'rin belgisi: " + ", ".join("{" + u + "}" for u in unknown))
    return PLACEHOLDER.sub(lambda m: values[m.group(1)], template)


def show_diff(old, new, name):
    lines = list(difflib.unified_diff(old.decode("utf-8", "replace").splitlines(), new.decode("utf-8", "replace").splitlines(),
                                      f"loyihada/{name}", f"kit/{name}", lineterm="", n=1))
    for line in lines[:MAX_DIFF]:
        print("    " + line, file=sys.stderr)
    if len(lines) > MAX_DIFF:
        print(f"    ... yana {len(lines) - MAX_DIFF} qator", file=sys.stderr)


def split_csv(text):
    return [p.strip() for p in text.split(",") if p.strip()]


def str_list(value, item_ok=lambda s: s.strip() != ""):
    return isinstance(value, list) and all(isinstance(x, str) and item_ok(x) for x in value)


def regex_ok(text):
    try:
        re.compile(text)
    except re.error:
        return False
    return text != ""  # bo'sh regex har yo'lga mos kelib, hamma PR ni sezgir qilib qo'yardi


def rel_path_ok(text):
    return text.strip() != "" and not text.startswith("/") and ".." not in text.split("/")


CHECKS = {  # kalit -> (yaroqlilik, tushuntirish): skriptlar yaroqsiz qiymatni jimgina standartga almashtirardi, o'rnatuvchi esa rad etadi
    "prefix": (lambda v: isinstance(v, str) and PREFIX_OK.fullmatch(v) is not None, "harf yoki raqam bilan boshlanadigan nom ([A-Za-z0-9._-])"),
    "gate_command": (lambda v: isinstance(v, str) and v.strip() != "" and "\n" not in v and "\r" not in v, "bo'sh bo'lmagan bir qatorli buyruq"),
    "reviewer": (lambda v: isinstance(v, str) and AGENT_OK.fullmatch(v) is not None, "agent va fayl nomi ([A-Za-z0-9_-], harf yoki raqam bilan boshlanadi)"),
    "source_labels": (lambda v: str_list(v, lambda s: s.strip() != "" and ":" not in s and "\n" not in s) and len(v) > 0,
                      "bo'sh bo'lmagan yorliqlar ro'yxati (yorliqda ':' bo'lmaydi)"),
    "sensitive": (lambda v: str_list(v, regex_ok), "bo'sh bo'lmagan to'g'ri regexlar ro'yxati"),
    "submodules": (lambda v: str_list(v), "bo'sh bo'lmagan papka nomlari ro'yxati"),
    "spec_paths": (lambda v: str_list(v, rel_path_ok), "repo ildiziga nisbiy yo'llar ro'yxati (papka yoki fayl, '/' bilan boshlanmaydi, '..' yo'q)"),
    "spec_prefix": (lambda v: isinstance(v, str) and v != "", "bo'sh bo'lmagan satr"),
    "spec_extra": (lambda v: str_list(v, rel_path_ok), "repo ildiziga nisbiy yo'llar ro'yxati ('/' bilan boshlanmaydi, '..' yo'q)"),
    "quality_log": (lambda v: isinstance(v, dict), "JSON obyekt"),
}


def cli_values(args):
    """Faqat berilgan CLI optsiyalari (berilmagani kalit sifatida yo'q)."""
    out = {}
    for key, value in (("prefix", args.prefix), ("gate_command", args.gate_command), ("reviewer", args.reviewer)):
        if value is not None:
            out[key] = value.strip()
    for key, value in (("source_labels", args.source_labels), ("submodules", args.submodules), ("spec_paths", args.spec_paths)):
        if value is not None:
            out[key] = split_csv(value)
    for key, value in (("sensitive", args.sensitive), ("spec_extra", args.spec_extra)):
        if value:
            out[key] = list(value)
    return out


def resolve_config(root, args):
    """config.json mazmuni: mavjud kalitlar saqlanadi, yangi kalitlar CLI > mavjud > standart. Qaytaradi: (yangi, mavjud, CLI mavjud qiymatni o'zgartirgan kalitlar)."""
    try:
        existing = json.loads((root / CONFIG).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise Fail(f"{CONFIG} o'qilmadi: {e}")
    if not isinstance(existing, dict):
        raise Fail(f"{CONFIG}: JSON obyekt kutilgan")
    given = cli_values(args)
    cfg = dict(existing)
    for key, default in DEFAULTS.items():
        if key in given:
            cfg[key] = given[key]
        elif cfg.get(key) is None:
            cfg[key] = copy.deepcopy(default)
    for key, (ok, why) in CHECKS.items():
        if not ok(cfg[key]):
            raise Fail(f"`{key}` qiymati yaroqsiz ({cfg[key]!r}): {why}; " + (f"{FLAGS[key]} bering yoki " if key in FLAGS else "") + f"{CONFIG} ni tuzating")
    changed = [k for k in given if existing.get(k) is not None and existing[k] != given[k]]
    return cfg, existing, changed


def read_kit(rel):
    try:
        return (KIT / rel).read_bytes()
    except OSError as e:
        raise Fail(f"to'plam fayli o'qilmadi: {rel} ({e.strerror or e})")


def kit_tests():
    names = sorted(p.name for p in (KIT / "tests").glob("test_*.py") if p.name != "test_install.py")
    if not names:
        raise Fail("to'plamda test fayllari yo'q (tests/test_*.py)")
    return names


def merged_gitignore(old, line):
    """Mavjud .gitignore: foydalanuvchi qatorlari saqlanadi, `__pycache__/` yo'q bo'lsa oxiriga qo'shiladi; bor bo'lsa baytlar o'zgarmaydi (qayta o'rnatish ham)."""
    if any(ln.strip() in GITIGNORE_HAS for ln in old.decode("utf-8", "replace").splitlines()):
        return old
    return old + (b"" if not old or old.endswith(b"\n") else b"\n") + line


def tail(text, n=15):
    return "\n".join(text.strip().splitlines()[-n:])


def run_step(title, cmd, root, timeout=None):
    t = time.time()
    try:
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")  # __pycache__ loyiha daraxtini va darvoza izi xeshini o'zgartirmasin
        r = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True, errors="replace", timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        print(f"  XATO {title}: {timeout} s dan oshdi", file=sys.stderr)
        return False
    out = (r.stdout + r.stderr).strip("\n")
    if r.returncode != 0:
        print(f"  XATO {title} (kod {r.returncode}):\n{tail(out, 40)}", file=sys.stderr)
        return False
    summary = " | ".join(line for line in out.splitlines()[-3:] if line.strip())
    print(f"  ok {title} ({time.time() - t:.1f} s)" + (f": {summary}" if summary else ""))
    return True


def next_steps(cfg):
    label, gate, reviewer = cfg["source_labels"][0], cfg["gate_command"], cfg["reviewer"]
    return f"""
Keyingi qadamlar:
  1. O'zgarishlarni feature branchda commit qiling va PR orqali kiriting (yangi hook va agent fayli faqat yangi Claude Code sessiyasida yuklanadi):
       git add .claude && git commit -m "ko'rik darvozasi"
  2. Darvoza skriptiga ({gate}) darvoza izini ulang (iz PR head daraxtiga bog'lanadi; yurish davomida fayl o'zgarsa yozilmaydi):
       boshida:  tree=$(python3 .claude/quality/gate_stamp.py --tree); python3 .claude/quality/gate_stamp.py --clear
       yashil:   python3 .claude/quality/gate_stamp.py --write --tree "$tree"
       qizil:    python3 .claude/quality/gate_stamp.py --clear
     Shu darvozaga qo'shing: python3 -B -m unittest discover -s .claude/quality/tests (-B shart: testlar yoniga __pycache__ yozilsa darvoza boshidagi daraxt
     xeshi o'zgarib, iz yozilmaydi; o'rnatilgan .claude/quality/.gitignore ham buni yopadi); ishlatilsa python3 .claude/quality/quality_log.py --check
     (topilmalar jurnali) va python3 .claude/quality/spec_guard.py (config spec_paths bo'sh bo'lmasa).
  3. CLAUDE.md ga qoida qo'shing, masalan: "Har PR ga bitta mustaqil ko'rik ({reviewer} agenti; unga faqat PR raqami va repo papkasi beriladi). 2-ko'rik
     faqat 1-ko'rikda bloker yoki major bo'lsa; 3-ko'rik faqat foydalanuvchi "ha" degach: python3 .claude/quality/review_stamp.py --pr N --allow.
     Merge faqat gh pr merge <N> --merge orqali; .claude/hooks/merge-guard.py va review-rounds.py ni chetlab o'tmang (blok = {gate} va ko'rikni PR ning
     oxirgi kodida qayta ishga tushirish)."
  4. Har PR tavsifida `{label}: <manba>` qatori bo'lsin: usiz ko'rik izi yozilmaydi."""


def install(args):
    root = Path(args.project).resolve()
    code, top = git(root, "rev-parse", "--show-toplevel")
    if code != 0 or Path(top).resolve() != root:
        raise Fail(f"{root} git repo ildizi emas" + (f" (repo ildizi: {top})" if top else ""))
    absent = [p for p in (CONFIG, GUARD_HOOK) if not (root / p).is_file()]
    if absent:
        raise Fail(f"git-guard o'rnatilmagan ({', '.join(absent)} yo'q). Avval: python3 {GIT_GUARD} {root}")

    cfg, existing, changed = resolve_config(root, args)
    cfg_text = render(cfg)
    agent = render_agent(read_kit(TEMPLATE).decode("utf-8"),
                         {"reviewer": cfg["reviewer"], "source_label": cfg["source_labels"][0].strip(), "gate_command": cfg["gate_command"].strip()})

    # 1) rejalashtirish: hech narsa yozilmaydi
    entries = [(dst, read_kit(src), exe) for src, dst, exe in FILES]
    entries += [(f"{TESTS_DST}/{name}", read_kit(f"tests/{name}"), False) for name in kit_tests()]
    entries.append((GITIGNORE, GITIGNORE_TEXT.encode("utf-8"), False))
    entries.append((f"{AGENTS}/{cfg['reviewer']}.md", agent.encode("utf-8"), False))
    plan, conflicts = [], []
    for dst, new, exe in entries:
        target = root / dst
        if not target.exists():
            plan.append((target, new, exe, "yangi"))
            continue
        old = target.read_bytes()
        if dst == GITIGNORE:  # foydalanuvchi tahrir qilgan bo'lishi mumkin: ziddiyat emas, yetishmayotgan qator qo'shiladi
            merged = merged_gitignore(old, new)
            plan.append((target, None if merged == old else merged, exe, "o'zgarmagan" if merged == old else "qo'shiladi"))
        elif old == new:
            plan.append((target, None, exe, "o'zgarmagan"))
        else:
            plan.append((target, new, exe, "ustiga yoziladi (--force)"))
            conflicts.append((dst, old, new))
    cfg_path = root / CONFIG
    if existing == cfg:
        plan.append((cfg_path, None, False, "o'zgarmagan"))
    elif changed:  # CLI mavjud qiymatni o'zgartiradi: ustiga yozish
        plan.append((cfg_path, cfg_text.encode("utf-8"), False, "ustiga yoziladi (--force)"))
        conflicts.append((CONFIG, render(existing).encode("utf-8"), cfg_text.encode("utf-8")))
    else:  # faqat yangi kalitlar qo'shiladi, mavjudlari o'zgarmaydi
        keys = ", ".join(k for k in cfg if existing.get(k) != cfg[k])
        plan.append((cfg_path, cfg_text.encode("utf-8"), False, f"yangi kalitlar qo'shiladi: {keys}"))

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
    print(f"{prefix}{SETTINGS}: " + ("PreToolUse " + ", ".join(added) + " yozuvi qo'shiladi" if added else "o'zgarmagan (yozuvlar bor)"))
    print(f"{prefix}prefix = {cfg['prefix']}, reviewer = {cfg['reviewer']}, gate_command = {cfg['gate_command']}, source_labels = {','.join(cfg['source_labels'])}")
    if args.dry_run:
        return 0

    # 2) yozish
    for target, content, exe, _ in plan:
        if content is not None:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        if exe and target.exists():
            target.chmod(target.stat().st_mode | 0o111)
    if added:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(render(settings), encoding="utf-8")
    ignored = [rel for rel in (os.path.relpath(t, root) for t, _, _, _ in plan) if git(root, "check-ignore", "-q", rel)[0] == 0]
    if ignored:
        print(f"OGOHLANTIRISH: .gitignore bu fayllarni yopadi, ular commit bo'lmaydi: {', '.join(ignored)}", file=sys.stderr)
    old_agent = existing.get("reviewer")
    if isinstance(old_agent, str) and old_agent != cfg["reviewer"] and (root / AGENTS / f"{old_agent}.md").exists():
        print(f"OGOHLANTIRISH: eski agent fayli qoldi: {AGENTS}/{old_agent}.md (kerak bo'lmasa o'chiring)", file=sys.stderr)

    # 3) tekshirish
    if not args.skip_tests:
        print("  testlar ishga tushmoqda (taxminan 5 daqiqa; o'tkazib yuborish: --skip-tests) ...", flush=True)
        test_cmd = [sys.executable, "-B", "-m", "unittest", "discover", "-s", TESTS_DST, "-p", "test_*.py"]
        if not run_step(f"testlar ({TESTS_DST})", test_cmd, root, TESTS_TIMEOUT):
            return 1

    print(next_steps(cfg))
    return 0


def main():
    ap = argparse.ArgumentParser(description="review-gate to'plamini loyihaga o'rnatadi (git-guard dan keyin)")
    ap.add_argument("project", help="loyiha (git repo) ildizi")
    ap.add_argument("--prefix", help=f"iz nomlari va PR izohi belgisi prefiksi (standart: {DEFAULTS['prefix']}; mavjud config.json dagi saqlanadi)")
    ap.add_argument("--reviewer", help=f"ko'rikchi agent nomi, .claude/agents/<nom>.md (standart: {DEFAULTS['reviewer']})")
    ap.add_argument("--gate-command", help=f"loyiha darvozasi buyrug'i, xabarlarda ko'rinadi (standart: {DEFAULTS['gate_command']})")
    ap.add_argument("--source-labels", help="PR tavsifidagi manba qatori yorliqlari, vergul bilan (standart: Source; ro'yxat standartni almashtiradi)")
    ap.add_argument("--sensitive", action="append", metavar="REGEX",
                    help="sezgir yo'l regexi (takrorlanadi; berilsa standart ro'yxatni almashtiradi: ^\\.claude/, ^\\.githooks/, ^\\.github/workflows/)")
    ap.add_argument("--submodules", help="alohida PR lari bor submodule papkalari, vergul bilan")
    ap.add_argument("--spec-paths", help="spec test yo'llari (papka yoki fayl), vergul bilan, masalan tests/spec/,e2e/spec/ (spec_guard.py uchun)")
    ap.add_argument("--spec-extra", action="append", metavar="PATH", help="`spec:` commit yana tegishi mumkin bo'lgan yo'l (takrorlanadi)")
    ap.add_argument("--force", action="store_true", help="boshqacha mavjud fayllar va config.json qiymatlari ustiga yozadi")
    ap.add_argument("--dry-run", action="store_true", help="nima qilinishini ko'rsatadi, hech narsa yozmaydi")
    ap.add_argument("--skip-tests", action="store_true", help="o'rnatilgan testlarni ishga tushirmaydi (~5 daqiqa tejaydi)")
    try:
        return install(ap.parse_args())
    except Fail as e:
        print(f"xato: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
