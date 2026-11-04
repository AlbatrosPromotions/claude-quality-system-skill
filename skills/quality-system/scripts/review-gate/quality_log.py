#!/usr/bin/env python3
"""Topilmalar jurnalini tekshiradi ("topilma → qoida" halqasi): jurnal formati va qoida majburiyati. Bu docstring jurnal spetsifikatsiyasi.

  `## Findings` jadvali, ustunlar `Date | PR | Category | Severity | Caught by | Rule`: Date YYYY-MM-DD; PR `#N` yoki `-`;
  Category `area.kind` (kichik harf, raqam, kamida bitta nuqta); Severity blocker | major | minor; "Caught by" bo'sh emas;
  Rule: `-`, `planned: <matn>`, `accepted: <matn>` yoki nomlangan tekshiruv: matnda kamida bitta backtick ichidagi token, u git kuzatadigan
  tekshiruv fayli (`git ls-files`, ildizga nisbatan; papka, submodule, ignor qilingan va kuzatilmagan fayl emas; token `tools/x.py --check`
  shaklida bo'lsa yo'l birinchi so'z) yoki darvoza skriptidagi `stage <nom>` qatoridagi bosqich nomi (faqat config `quality_log.gate_script`
  berilganda; berilmasa bosqich nomi hisobga olinmaydi).
  Fayl faqat tekshiruv bo'lsa hisobga olinadi (prompt va hujjat emas). Standart: prefikslar tests/ test/ spec/ specs/ e2e/ tools/ scripts/ ci/
  .claude/hooks/ .claude/quality/ .githooks/ .github/workflows/; nomlar test_*.py *_test.py *_test.go *Test.php *Test.java *.spec.ts *.test.ts
  *.spec.js *.test.js *.spec.tsx *.test.tsx *_spec.rb; fayl Makefile; `.md` fayl hech qachon tekshiruv emas. Config `quality_log.check_prefixes`,
  `check_patterns` (nomda "/" bo'lsa to'liq yo'lga, aks holda fayl nomiga qo'llanadi), `check_files` ro'yxatni almashtiradi (to'ldirmaydi).
  Majburiyat: ikki va undan ko'p qatorli yoki kamida bitta `blocker` qatorli kategoriyada kamida bitta qator nomlangan tekshiruv, `accepted:`
  (faqat `blocker` qatori yo'q kategoriyada) yoki muddati o'tmagan `planned:` bo'lishi shart; `planned:` qator faqat sanasidan 7 kun ichida
  hisobga olinadi. Sanasidan 7 kundan oshgan har `planned:` qator (kategoriya qatorlari soni va darajasidan qat'i nazar) qizil: bajarilmagan va'da.
  Takrorlanish: shunday kategoriyada oxirgi qoida/yopilish qatoridan (nomlangan, `accepted:`, muddati o'tmagan `planned:`) keyin faylda
  yana 2 va undan ko'p qator kelsa qizil: qoida ishlamadi yoki yopilish eskirdi.
  Takroriy qator: Date, PR, Category, Severity va "Caught by" bir xil ikki qator qizil (ehtimol merge artefakti).
  Jadval uzilishi: bo'lim jadvali tugagach `|` bilan boshlanadigan qator (jadval ichidagi bo'sh qator) qizil, aks holda keyingi qatorlar jim tekshirilmay qoladi.
  Tizim `minor` qoidasi: config `quality_log.minor_rule_from` (YYYY-MM-DD) berilsa, shu sanadan boshlab `minor` darajali va kategoriyasining birinchi
  qismi `quality_log.system_prefixes` da bo'lgan qator qizil: tizim tomonidagi `minor` topilma PR izohida qoladi, jurnalga yozilmaydi.
  `## Critic canary` jadvali (bo'lim butunlay bo'lmasligi mumkin; ustunlar `Date | Branch | Planted | Caught | Missed (category) | Rate | Note`):
  Planted va Caught butun son, Caught <= Planted, Rate = `Caught/Planted`.

  python3 quality_log.py --check [--file PATH] [--today YYYY-MM-DD] [--root PATH] [--config PATH]

Jurnal fayli: --file > config `quality_log.file` > `docs/quality-log.md` (ildizga nisbatan). Ildiz: --root, standart skript papkasining git ildizi
(yo'q bo'lsa joriy papka). Sozlama: --config > $QUALITY_CONFIG > <skript papkasi>/config.json; fayl yo'q yoki kalit yo'q = standart.
Muammo bo'lsa satr raqamli o'zbekcha hisobot va chiqish kodi 1. Faqat standart kutubxona.
"""
import argparse
import datetime
import fnmatch
import functools
import json
import os
import posixpath
import re
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FINDINGS_HEADING = "## Findings"
CANARY_HEADING = "## Critic canary"
FINDINGS_COLUMNS = ["Date", "PR", "Category", "Severity", "Caught by", "Rule"]
CANARY_COLUMNS = ["Date", "Branch", "Planted", "Caught", "Missed (category)", "Rate", "Note"]
LEVELS = ("blocker", "major", "minor")
CATEGORY = re.compile(r"^[a-z0-9]+(\.[a-z0-9-]+)+$")
PR_REF = re.compile(r"^(#\d+|-)$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SEPARATOR_CELL = re.compile(r"^:?-+:?$")
PLANNED = "planned:"
ACCEPTED = "accepted:"
PLANNED_DAYS = 7
BACKTICK = re.compile(r"`([^`]+)`")
GITLINK_MODE = "160000"
STAGE = re.compile(r"^[ \t]*stage[ \t]+([A-Za-z0-9][A-Za-z0-9_.-]*)(?=[ \t\r]|$)", re.MULTILINE)
# config `quality_log` kalitlari va standartlari; ro'yxatlar standartni almashtiradi. Prompt va hujjatlar (masalan `.claude/agents/*.md`) tekshiruv emas
DEFAULTS = {
    "file": "docs/quality-log.md",
    "gate_script": None,
    "minor_rule_from": None,
    "system_prefixes": ["ci", "docs", "test", "review", "quality", "agent", "style", "arch", "git", "build", "spec", "dev", "config", "dup", "seed"],
    "check_prefixes": ["tests/", "test/", "spec/", "specs/", "e2e/", "tools/", "scripts/", "ci/", ".claude/hooks/", ".claude/quality/", ".githooks/",
                       ".github/workflows/"],
    "check_patterns": ["test_*.py", "*_test.py", "*_test.go", "*Test.php", "*Test.java", "*.spec.ts", "*.test.ts", "*.spec.js", "*.test.js",
                       "*_spec.rb", "*.spec.tsx", "*.test.tsx"],
    "check_files": ["Makefile"],
}


def warn(message):
    print(f"quality_log: {message}", file=sys.stderr)


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


def parse_date(text):
    if not DATE.match(text):
        return None
    try:
        return datetime.date.fromisoformat(text)
    except ValueError:
        return None


def text_value(section, key):
    value = section.get(key)
    if value is None:
        return DEFAULTS[key]
    if isinstance(value, str) and value.strip():
        return value
    warn(f"config quality_log.{key}: bo'sh bo'lmagan satr kutilgan, standart ishlatiladi")
    return DEFAULTS[key]


def list_value(section, key):
    value = section.get(key)
    if value is None:
        return list(DEFAULTS[key])
    # bo'sh satr prefiksi har yo'lga mos kelib, har faylni tekshiruv qilib qo'yardi
    if isinstance(value, list) and all(isinstance(item, str) and item.strip() for item in value):
        return list(value)
    warn(f"config quality_log.{key}: bo'sh bo'lmagan satrlar ro'yxati kutilgan, standart ishlatiladi")
    return list(DEFAULTS[key])


def settings_from(cfg):
    """config["quality_log"] dan to'liq sozlama: yo'q kalit = standart, noto'g'ri qiymat = standart + stderr da qator."""
    section = cfg.get("quality_log")
    if section is not None and not isinstance(section, dict):
        warn("config `quality_log` JSON obyekt bo'lishi kerak, standart ishlatiladi")
    if not isinstance(section, dict):
        section = {}
    settings = {key: text_value(section, key) for key in ("file", "gate_script")}
    settings.update({key: list_value(section, key) for key in ("system_prefixes", "check_prefixes", "check_patterns", "check_files")})
    raw = section.get("minor_rule_from")
    settings["minor_rule_from"] = parse_date(raw) if isinstance(raw, str) else None
    if raw is not None and settings["minor_rule_from"] is None:
        warn("config quality_log.minor_rule_from: YYYY-MM-DD sana kutilgan, tizim `minor` qoidasi o'chirilgan")
    return settings


def default_root():
    """Skript papkasining git ildizi; git repo bo'lmasa (yoki git yo'q) joriy papka."""
    try:
        r = subprocess.run(["git", "-C", SCRIPT_DIR, "rev-parse", "--show-toplevel"], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
        top = r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        top = ""
    return Path(top or os.getcwd()).resolve()


def split_row(line):
    """'| a | b |' -> ['a', 'b']; qator '|' bilan boshlanib tugamasa None. Katak ichidagi `\\|` belgi bo'lib qoladi."""
    text = line.strip()
    if len(text) < 2 or not text.startswith("|") or not text.endswith("|"):
        return None
    return [cell.strip().replace("\\|", "|") for cell in re.split(r"(?<!\\)\|", text[1:-1])]


def read_table(lines, heading, columns, errors, label, required):
    """Bo'lim jadvalidan [(satr raqami, [kataklar])] qaytaradi; sarlavha va ustunlar xatosi errors ga. required=False: bo'lim butunlay yo'q bo'lsa xato emas."""
    start = next((i for i, line in enumerate(lines) if line.strip() == heading), None)
    if start is None:
        if required:
            errors.append((0, f"`{heading}` bo'limi topilmadi"))
        return []
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    first = next((i for i in range(start + 1, end) if lines[i].lstrip().startswith("|")), None)
    if first is None:
        errors.append((start + 1, f"`{heading}` bo'limida jadval yo'q (ustunlar: {' | '.join(columns)})"))
        return []
    block = []
    for i in range(first, end):
        if not lines[i].lstrip().startswith("|"):
            stray = next((j for j in range(i, end) if lines[j].lstrip().startswith("|")), None)
            if stray is not None:
                errors.append((stray + 1, f"jadval uzilgan: {block[-1] + 1}-satrdan keyingi qatorlar tekshirilmaydi ({i + 1}-satr `|` bilan boshlanmaydi; jadval ichida bo'sh qator bo'lmasin)"))
            break
        block.append(i)
    header = split_row(lines[block[0]])
    if header != columns:
        errors.append((block[0] + 1, f"{label} jadvali sarlavhasi `{' | '.join(columns)}` bo'lishi kerak"))
        return []
    separator = split_row(lines[block[1]]) if len(block) > 1 else None
    if not separator or not all(SEPARATOR_CELL.match(cell) for cell in separator):
        errors.append((block[0] + 2, f"{label} jadvalida sarlavhadan keyin `|---|` ajratgich qatori kerak"))
        return []
    rows = []
    for i in block[2:]:
        cells = split_row(lines[i])
        if cells is None:
            errors.append((i + 1, "jadval qatori `|` bilan tugamagan"))
        elif len(cells) != len(columns):
            errors.append((i + 1, f"{label} qatorida {len(cells)} katak, {len(columns)} bo'lishi kerak (`|` belgisi kerak bo'lsa `\\|` yozing)"))
        else:
            rows.append((i + 1, cells))
    return rows


def stage_names(root, gate_script):
    """Darvoza skriptidagi `stage <nom> ...` qatorlaridan bosqich nomlari; skript berilmagan yoki yo'q bo'lsa bo'sh."""
    if not gate_script:
        return set()
    try:
        return set(STAGE.findall((root / gate_script).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


@functools.lru_cache(maxsize=None)
def tracked_files(root):
    """Ildizdagi git kuzatadigan fayllar (`git ls-files -s`); submodule gitlink (160000) papka: fayl emas. Git repo bo'lmasa bo'sh: hech qanday yo'l nomlangan tekshiruv emas."""
    try:
        out = subprocess.run(["git", "-C", str(root), "ls-files", "-s", "-z"], stdin=subprocess.DEVNULL, capture_output=True, check=True, timeout=60).stdout
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return frozenset()
    names = set()
    for entry in out.decode("utf-8", "replace").split("\0"):
        info, _, name = entry.partition("\t")
        if name and not info.startswith(GITLINK_MODE + " "):
            names.add(name)
    return frozenset(names)


def is_repo_path(root, relative):
    """`relative` git kuzatadigan fayl: papka (keng nom qoidani nasrga qaytaradi), ignor qilingan va kuzatilmagan fayl, mutlaq yo'l va `..` emas."""
    if relative.startswith("/"):
        return False
    normal = posixpath.normpath(relative)
    return normal not in (".", "..") and not normal.startswith("../") and normal in tracked_files(root)


def is_check_path(relative, settings):
    """Kuzatiladigan fayl tekshiruv ekanini yo'ldan aniqlaydi: test/skript papkalari, test fayl nomlari va aniq fayllar; `.md` hech qachon (README, fikstura hujjati)."""
    normal = posixpath.normpath(relative)
    if normal.lower().endswith(".md"):
        return False
    return normal.startswith(tuple(settings["check_prefixes"])) or normal in {posixpath.normpath(f) for f in settings["check_files"]} or any(
        fnmatch.fnmatchcase(normal if "/" in pattern else posixpath.basename(normal), pattern) for pattern in settings["check_patterns"])


def names_existing_target(text, root, settings, stages):
    for token in BACKTICK.findall(text):
        token = token.strip()
        # `tools/x.py --check` shaklida yo'l birinchi so'z
        if token and (token in stages or (is_repo_path(root, token.split()[0]) and is_check_path(token.split()[0], settings))):
            return True
    return False


def named_hint(settings):
    """Nomlangan tekshiruv xatosining tushuntirishi: faqat haqiqatan qabul qilinadigan narsalar (gate_script bo'lsa bosqich nomi ham)."""
    paths = ", ".join(settings["check_prefixes"] + settings["check_patterns"] + settings["check_files"])
    stage = f"darvoza skripti `{settings['gate_script']}` dagi bosqich nomi (masalan `lint`) yoki " if settings["gate_script"] else ""
    return (f"backtick ichida: {stage}git kuzatadigan tekshiruv fayli yo'li, repo ildiziga nisbatan: {paths}; "
            ".md, prompt va hujjat fayli tekshiruv emas; skript ajrata olmaydigan vaziyat uchun `accepted: <matn>`")


def classify_rule(rule, date, today, root, settings, stages):
    """(tur, [xabarlar]); tur: none | named | accepted | planned. Xato qatorning turi none: majburiyatni bajarmaydi."""
    if not rule:
        return "none", ["Rule bo'sh: `-`, `planned: <matn>`, `accepted: <matn>` yoki nomlangan tekshiruvni yozing"]
    if rule == "-":
        return "none", []
    if rule.startswith(PLANNED):
        problems = []
        if not rule[len(PLANNED):].strip():
            problems.append("`planned:` dan keyin sessiya yoki sana yozilishi kerak")
        if date and (today - date).days > PLANNED_DAYS:
            problems.append(f"`planned:` va'dasi muddati o'tgan ({(today - date).days} kun > {PLANNED_DAYS}): qoidani quring va Rule ustuniga tekshiruv nomini yozing")
        return "planned", problems
    if rule.startswith(ACCEPTED):
        if not rule[len(ACCEPTED):].strip():
            return "none", ["`accepted:` dan keyin sabab yozilishi kerak"]
        return "accepted", []
    if re.match(r"(?i)^planned\b", rule):
        return "none", [f"Rule `{rule}` `planned:` (kichik harf, ikki nuqta) bilan boshlanishi kerak"]
    if re.match(r"(?i)^accepted", rule):
        return "none", [f"Rule `{rule}` `accepted:` (kichik harf, ikki nuqta) bilan boshlanishi kerak"]
    if names_existing_target(rule, root, settings, stages):
        return "named", []
    kind = "tekshiruv fayli yoki darvoza bosqichini" if settings["gate_script"] else "tekshiruv faylini"
    return "none", [f"nomlangan tekshiruv mavjud {kind} ko'rsatmaydi ({named_hint(settings)})"]


def is_live(row, today):
    """`planned:` qator sanasidan 7 kun ichida (kelajak sanasi ham); sanasiz qator hisobga olinmaydi."""
    return row["kind"] == "planned" and row["date"] is not None and (today - row["date"]).days <= PLANNED_DAYS


def check_duplicates(rows, errors):
    """Birinchi besh katak bir xil qatorlar: "keep both sides" merge dan qolgan eski nusxa yopilgan nusxa yonida qolmasin."""
    seen = {}
    for lineno, cells in rows:
        key = tuple(cells[:5])
        if key in seen:
            errors.append((lineno, (
                f"{seen[key]}-satr bilan takroriy (Date, PR, Category, Severity, Caught by bir xil), ehtimol merge artefakti: "
                "yopilgan (nomlangan yoki `accepted:`) nusxani qoldiring, ikkinchisini o'chiring")))
        else:
            seen[key] = lineno


def is_unjournaled_minor(date, level, category, settings):
    """Tizim kategoriyasining `minor` topilmasi minor_rule_from sanasida yoki undan keyin; prefiks = birinchi nuqtagacha qism. Sanasi o'qilmagan qator faqat Date xatosi bilan qizil."""
    since = settings["minor_rule_from"]
    return bool(since) and date is not None and date >= since and level == "minor" and category.split(".", 1)[0] in settings["system_prefixes"]


def check_findings(rows, today, root, settings, errors):
    stages = stage_names(root, settings["gate_script"])
    check_duplicates(rows, errors)
    parsed = []
    for lineno, (date_text, pr, category, level, caught, rule) in rows:
        date = parse_date(date_text)
        if date is None:
            errors.append((lineno, f"Date `{date_text}` YYYY-MM-DD shaklida va haqiqiy sana bo'lishi kerak"))
        if not PR_REF.match(pr):
            errors.append((lineno, f"PR `{pr}` `#N` yoki `-` bo'lishi kerak"))
        category_ok = bool(CATEGORY.match(category))
        if not category_ok:
            errors.append((lineno, f"Category `{category}` `area.kind` shaklida bo'lishi kerak (kichik harf, raqam, kamida bitta nuqta; masalan `ui.corner-bleed`)"))
        if level not in LEVELS:
            errors.append((lineno, f"Severity `{level}`: {', '.join(LEVELS)} dan biri bo'lishi kerak"))
        if is_unjournaled_minor(date, level, category, settings):
            errors.append((lineno, (
                f"tizim kategoriyasi `{category}` ning `minor` topilmasi {settings['minor_rule_from'].isoformat()} dan jurnalga yozilmaydi: "
                "PR izohida qoldiring yoki daraja `major`/`blocker` bo'lsa o'zgartiring")))
        if not caught:
            errors.append((lineno, "\"Caught by\" bo'sh"))
        kind, problems = classify_rule(rule, date, today, root, settings, stages)
        errors.extend((lineno, message) for message in problems)
        if category_ok:
            parsed.append({"line": lineno, "category": category, "level": level, "date": date, "kind": kind})

    by_category = {}
    for row in parsed:
        by_category.setdefault(row["category"], []).append(row)
    for category, group in by_category.items():
        reasons = []
        if len(group) >= 2:
            reasons.append(f"{len(group)} qator")
        if any(row["level"] == "blocker" for row in group):
            reasons.append("blocker")
        if not reasons:
            continue
        # blocker kategoriya faqat deterministik qoida bilan yopiladi: `accepted:` uni yopmaydi
        accepted = ("named",) if "blocker" in reasons else ("named", "accepted")
        closing = [row for row in group if row["kind"] in accepted or is_live(row, today)]
        if not closing:
            deferred = [(row["line"], (today - row["date"]).days) for row in group if row["kind"] == "planned" and row["date"]]
            lines = ", ".join(str(row["line"]) for row in group)
            expired = [f"{lineno}-satr {days} kun" for lineno, days in deferred]
            note = f"; `planned:` muddati o'tgan ({'; '.join(expired)} > {PLANNED_DAYS} kun)" if expired else ""
            errors.append((group[0]["line"], (
                f"Category `{category}` ({' va '.join(reasons)}; satrlar {lines}) uchun nomlangan tekshiruv, `accepted:` (blocker bo'lmasa) yoki amaldagi `planned:` yo'q{note}: "
                "Rule ustuniga tekshiruv nomi va faylini yozing (test, linter qoidasi, skript) yoki qoidani shu PR da quring")))
            continue
        last = closing[-1]
        later = group[group.index(last) + 1:]
        if len(later) >= 2:
            lines = ", ".join(str(row["line"]) for row in later)
            errors.append((later[-1]["line"], (
                f"Category `{category}` {last['line']}-satrdagi qoida yoki yopilishdan keyin yana {len(later)} marta uchradi (satrlar {lines}): "
                "yangi qoida quring yoki eng oxirgi qatorga yangi `accepted:`/`planned:` yozing")))


def check_canary(rows, errors):
    for lineno, (_date, _branch, planted_text, caught_text, _missed, rate, _note) in rows:
        planted = int(planted_text) if re.fullmatch(r"\d+", planted_text) else None
        caught = int(caught_text) if re.fullmatch(r"\d+", caught_text) else None
        if planted is None:
            errors.append((lineno, f"Planted `{planted_text}` butun son bo'lishi kerak"))
        if caught is None:
            errors.append((lineno, f"Caught `{caught_text}` butun son bo'lishi kerak"))
        if planted is not None and caught is not None and caught > planted:
            errors.append((lineno, f"Caught ({caught}) Planted ({planted}) dan katta bo'lishi mumkin emas"))
        if not re.fullmatch(r"\d+/\d+", rate):
            errors.append((lineno, f"Rate `{rate}` `N/M` shaklida bo'lishi kerak (Caught/Planted)"))
        elif planted is not None and caught is not None and rate != f"{caught}/{planted}":
            errors.append((lineno, f"Rate `{rate}` Caught/Planted = `{caught}/{planted}` bo'lishi kerak"))


def check(text, today, root, settings):
    """(muammolar [(satr raqami, matn)] satr bo'yicha tartiblangan, topilma qatorlari soni, kanareyka qatorlari soni); muammolar bo'sh = jurnal joyida."""
    lines = text.splitlines()
    errors = []
    findings = read_table(lines, FINDINGS_HEADING, FINDINGS_COLUMNS, errors, "Findings", True)
    check_findings(findings, today, Path(root).resolve(), settings, errors)
    canary = read_table(lines, CANARY_HEADING, CANARY_COLUMNS, errors, "Critic canary", False)
    check_canary(canary, errors)
    return sorted(errors, key=lambda item: item[0]), len(findings), len(canary)


def display(path, root):
    """Hisobotdagi fayl nomi: ildiz ichida bo'lsa ildizga nisbatan yo'l, aks holda berilgan yo'l."""
    try:
        return Path(path).resolve().relative_to(root).as_posix()
    except (OSError, ValueError):
        return str(path)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="jurnal formati va qoida majburiyatini tekshiradi")
    ap.add_argument("--file", help="jurnal fayli (standart: config quality_log.file, yo'q bo'lsa docs/quality-log.md; ildizga nisbatan)")
    ap.add_argument("--today", help="bugungi sana YYYY-MM-DD (testlar uchun; standart: tizim sanasi)")
    ap.add_argument("--root", help="repo ildizi (standart: skript papkasining git ildizi, yo'q bo'lsa joriy papka)")
    ap.add_argument("--config", help="config.json yo'li (standart: $QUALITY_CONFIG, keyin <skript papkasi>/config.json)")
    args = ap.parse_args(argv)
    if not args.check:
        ap.error("--check kerak")
    today = parse_date(args.today) if args.today else datetime.date.today()
    if today is None:
        ap.error("--today YYYY-MM-DD shaklida bo'lishi kerak")
    settings = settings_from(load_config(args.config))
    root = Path(args.root).resolve() if args.root else default_root()
    path = Path(args.file) if args.file else root / settings["file"]
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        print(f"quality_log QIZIL: {path} o'qilmadi: {exc}", file=sys.stderr)
        return 1
    errors, n_findings, n_canary = check(text, today, root, settings)
    name = display(path, root)
    if errors:
        print(f"quality_log QIZIL: {name} da {len(errors)} muammo (qoida: quality_log.py --help)", file=sys.stderr)
        for lineno, message in errors:
            print(f"  {name}:{lineno}: {message}" if lineno else f"  {name}: {message}", file=sys.stderr)
        return 1
    print(f"quality_log: {n_findings} findings, {n_canary} canary rows; format va qoida majburiyati joyida ({today.isoformat()})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
