#!/usr/bin/env python3
"""PreToolUse (Agent): ko'rikchi agent chaqiruvi ko'rik turlari chegarasidan tashqarida bo'lsa bloklaydi, ko'rik boshlanmasdan.

Kirish (stdin JSON): tool_name, tool_input {subagent_type, prompt, description}, cwd. Boshqa agent yoki PR raqamsiz brief: o'tkaziladi.
PR raqami brief matnidan (`PR #N`, `PR N`, `--pr N`, birinchi uchragani); config `submodules` dagi papka (orqasida yo'l davomi yo'q) repo
papkasi deb tilga olinsa submodule PR i (o'sha papkada tekshiriladi); `<papka>/04.md` kabi fayl havolasi loyiha repo si PR ini submodule ga olib ketmaydi.
Hukm `review_stamp.py --pr N --check` dan (hook yonidagi ../quality/ yoki bir papkadagi nusxa): chiqish kodi 4 (chegara) = blok (chiqish 2,
sabab stderr da); boshqa natija va xato o'tkaziladi, chunki chegarani iz yozishda skriptning o'zi baribir majburlaydi.
Sozlama: `--config` > $QUALITY_CONFIG > <hook papkasi>/../quality/config.json (reviewer, submodules); fayl yoki kalit yo'q = standart,
buzuq fayl = standart + ogohlantirish. SendMessage orqali qayta ko'rik bu hookdan o'tmaydi: uni ham skript to'xtatadi. Faqat standart kutubxona.
"""
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULTS = {"reviewer": "opus-reviewer", "submodules": []}
VALID = {
    "reviewer": lambda v: isinstance(v, str) and v.strip() != "",
    "submodules": lambda v: isinstance(v, list) and all(isinstance(x, str) for x in v),
}
PR_NUMBER = re.compile(r"(?i)(?:\bPR\s*#?\s*|--pr\s+)([0-9]+)")


def config_arg(argv):
    for i, a in enumerate(argv):
        if a == "--config" and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith("--config="):
            return a[len("--config="):]
    return None


def load_config(explicit=None):
    """--config > $QUALITY_CONFIG > <hook papkasi>/../quality/config.json. Fayl yoki kalit yo'q = standart; buzuq fayl = standart + bitta stderr qator."""
    path = explicit or os.environ.get("QUALITY_CONFIG") or os.path.join(HERE, "..", "quality", "config.json")
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
    cfg = dict(DEFAULTS)
    for key, ok in VALID.items():
        if data.get(key) is None:
            continue
        if ok(data[key]):
            cfg[key] = data[key]
        else:
            problems.append(f"{path}: `{key}` qiymati noto'g'ri")
    for p in problems:
        print(f"ogohlantirish: {p}; standart sozlama ishlatiladi", file=sys.stderr)
    return cfg


def stamp_script():
    """O'rnatilgan joylashuv (.claude/hooks va .claude/quality) yoki to'plam papkasi (yonma-yon)."""
    for p in (os.path.join(HERE, "..", "quality", "review_stamp.py"), os.path.join(HERE, "review_stamp.py")):
        if os.path.isfile(p):
            return os.path.normpath(p)
    return None


def names_dir(text, sub):
    """Matnda papka butun so'z bo'lib tilga olinganmi: oldida/orqasida yo'l davomi yo'q (jumla oxiridagi nuqta davom emas)."""
    return re.search(r"(?<![\w.-])" + re.escape(sub) + r"(?![\w/-]|\.[\w-])", text) is not None


def main(argv=None):
    explicit = config_arg(sys.argv[1:] if argv is None else argv)
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return 0
    if not isinstance(data, dict) or data.get("tool_name") != "Agent":
        return 0
    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict):
        return 0
    cfg = load_config(explicit)
    if tool_input.get("subagent_type") != cfg["reviewer"]:
        return 0
    text = f"{tool_input.get('description') or ''}\n{tool_input.get('prompt') or ''}"
    m = PR_NUMBER.search(text)
    if not m:
        return 0
    cwd = data.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        cwd = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    repo = cwd
    for sub in (s.strip().strip("/") for s in cfg["submodules"]):
        if sub and names_dir(text, sub) and os.path.isdir(os.path.join(cwd, sub)):
            repo = os.path.join(cwd, sub)
            break
    script = stamp_script()
    if not script:
        print("review-rounds: review_stamp.py topilmadi (hook yonidagi ../quality/ yoki shu papka): tur chegarasi tekshirilmadi", file=sys.stderr)
        return 0
    cmd = [sys.executable, script, "--pr", m.group(1), "--check"]
    env = dict(os.environ)
    if explicit:  # skript boshqa papkada ishlaydi: nisbiy yo'l hookning cwd siga nisbatan mutlaqlashtiriladi, ikkalasi bir faylni o'qisin
        cmd += ["--config", os.path.abspath(explicit)]
    elif env.get("QUALITY_CONFIG"):
        env["QUALITY_CONFIG"] = os.path.abspath(env["QUALITY_CONFIG"])
    try:
        r = subprocess.run(cmd, cwd=repo, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, errors="replace", timeout=25)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return 0
    if r.returncode == 4:
        reason = "\n".join(ln for ln in r.stderr.splitlines() if not ln.startswith("ogohlantirish:")).strip()
        print(f"Ko'rik bloklandi: {reason} Qoida: loyiha CLAUDE.md ko'rik qoidasi.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except Exception as e:  # hookning o'z xatosi ko'rikni to'xtatmasin: chegarani skriptning o'zi iz yozishda majburlaydi
        print(f"review-rounds: kutilmagan xato, o'tkazildi: {e!r}", file=sys.stderr)
        code = 0
    sys.exit(code)
