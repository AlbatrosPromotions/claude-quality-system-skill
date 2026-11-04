#!/usr/bin/env python3
"""PreToolUse (Bash): `gh pr merge` faqat darvoza izi (<prefix>-ci-ok) PR head daraxtiga mos va mustaqil ko'rik izi (<prefix>-review-ok-<PR>)
shu PR ga yaroqli bo'lsa o'tadi; aks holda exit 2: Claude Code buyruqni bajarmaydi va sababni modelga qaytaradi.

Kirish: stdin JSON {tool_name, tool_input: {command}, cwd, agent_type}. Bloklash: exit 2 va `Merge bloklandi: ...` stderr ga; ruxsat: exit 0.
Kirish o'qilmasa yoki buyruq bo'sh bo'lsa ruxsat; kutilmagan xato bo'lsa traceback va exit 1 (bloklamaydi): himoya vositasi ishni to'xtatib qo'ymasin.
Buyruq shell kabi bo'laklanadi (heredoc tanasi va izohlar tashlanadi, shlex), shuning uchun faqat haqiqiy buyruq tekshiriladi: `echo "gh pr merge 7"`,
PR matni yoki commit xabaridagi so'z merge emas. Oldidan VAR=qiymat, env/command/nohup/time/exec/nice/sudo/timeout bo'lishi mumkin.

Merge qoidasi:
  - bash/sh/zsh -c, eval, $(...), `...` ichidagi merge va quvur orqali shell ga berilgan `gh pr merge` matni bloklanadi (nishon PR ni ishonchli aniqlab bo'lmaydi);
  - `-R/--repo`, GH_REPO=, GH_HOST= bilan merge bloklanadi (iz qaysi repo ga tegishli ekani noma'lum); nishon faqat PR raqami (URL, branch, #N emas),
    nishonsiz = joriy branch PR i; nishon oldidagi literal `cd` lar repo papkasini belgilaydi ($, ` yoki - nishonli cd noma'lum = blok);
  - `gh pr view [N] --json number,headRefOid`; `<prefix>-ci-ok` ning birinchi maydoni PR head daraxtiga teng bo'lishi shart;
  - `<prefix>-review-ok-<PR>` = `<daraxt> <PR> <head> <vaqt> round=<k> blockers=<b> major=<m> minor=<n>`: PR shu PR, bloker 0, 1-tur izida muhim 0;
    iz daraxti PR head daraxtidan farq qilsa iz head i PR head ning ajdodi bo'lishi shart (ko'rikdan keyingi mayda commit lar faqat darvoza bilan).
  - `gh` bilan `pr` va `pr` bilan `merge` orasida bayroqlar bo'lishi mumkin (-R/--repo qiymati bilan, --help): `gh pr --repo o/r merge 5` ham merge, -R/--repo bloki unga ham tegadi.
Ikkinchi qoida (merge dan mustaqil): iz yozadigan skript faqat DASTUR sifatida ishga tushirilganda tekshiriladi (buyruqning o'zi yoki python/python3/python3.x ning
skript yoki -m argumenti, o'ramlardan, bash -c, eval va $(...) ichida ham); grep/cat/echo/git kabi boshqa dastur argumentidagi tilga olish oddiy matn:
  - `review_stamp.py` ni --head/--report bilan (izni yozadigan shakl) chaqirish faqat sozlamadagi reviewer agentidan (hook kirishidagi agent_type); --check va --allow hammaga ochiq;
  - `gate_stamp.py` ni --write bilan (qisqartmalari --wr... ham) chaqirish hech kimga ochiq emas: darvoza izini faqat darvoza skripti yozadi. Hook darvoza skriptining o'z
    chaqiruvini ko'rmaydi (asbob buyrug'i `make ci`), shuning uchun darvoza ishlayveradi; --tree, --check, --clear, --help ochiq.
Bu to'siq, atayin aldab o'tish shakllari (xargs, find -exec, o'zgaruvchi orqali yo'l, skript ichidagi chaqiruv) uning doirasida emas.
Sozlama (.claude/quality/config.json): prefix (quality), gate_command (make ci), reviewer (opus-reviewer). Tartib: --config > $QUALITY_CONFIG >
<hook papkasi>/../quality/config.json; fayl yoki kalit yo'q = standart, buzuq fayl = standart + bitta stderr qator. Faqat standart kutubxona.
"""
import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import traceback

DEFAULTS = {"prefix": "quality", "gate_command": "make ci", "reviewer": "opus-reviewer"}
VALID = {
    "prefix": lambda v: isinstance(v, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", v) is not None,
    "gate_command": lambda v: isinstance(v, str) and v.strip() != "",
    "reviewer": lambda v: isinstance(v, str) and v.strip() != "",
}
RULE = "Qoida: loyiha CLAUDE.md dagi merge qoidasi."
MAX_DEPTH = 8
OID = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
GIT_ENV_DROP = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_PREFIX", "GIT_COMMON_DIR")

GH_WORD = re.compile(r"\bgh\b")  # tez filtr: gh ham, review_stamp/gate_stamp ham yo'q buyruq tahlil qilinmaydi
GH_FLAGS = r"(?:-\S+\s+(?:[^\s-]\S*\s+)?)*"  # bayroq va (bo'lsa) uning qiymati: `-R o/r `, `--help `
MERGE_TEXT = re.compile(r"gh\s+" + GH_FLAGS + r"pr\s+" + GH_FLAGS + "merge")
GH_ENV = re.compile(r"(?:^|[^A-Za-z0-9_])(?:GH_REPO|GH_HOST)=")
STAMP_WORD = re.compile(r"(?<![A-Za-z0-9_])review_stamp(?![A-Za-z0-9_])")
WRITE_FLAG_TEXT = re.compile(r"--(?:he|hea|head|re|rep|repo|repor|report)(?![A-Za-z0-9_-])")
GATE_WORD = re.compile(r"(?<![A-Za-z0-9_])gate_stamp(?![A-Za-z0-9_])")
GATE_FLAG_TEXT = re.compile(r"--(?:w|wr|wri|writ|write)(?![A-Za-z0-9_-])")
PYTHON = re.compile(r"python(?:[0-9]+(?:\.[0-9]+)*)?")
STAMP_SCRIPT = re.compile(r"(review_stamp|gate_stamp)(?:\.py)?")
WRITE_FLAGS = {"review_stamp": (("head", "report"), 2), "gate_stamp": (("write",), 1)}  # izni yozadigan bayroqlar, qisqartmaning eng kichik uzunligi
GLUED_COMMENT = re.compile(r"#([A-Za-z0-9_.-]+)")
ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
CD_FLAGS = re.compile(r"^-[LPe@]+$")
SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "fish"}
SHELL_KEYWORDS = {"if", "then", "elif", "else", "while", "until", "do", "!", "{"}  # `then gh pr merge 7` ham buyruq
VALUE_LONG = {"--body", "--body-file", "--subject", "--author-email", "--match-head-commit"}  # gh pr merge: qiymat oladigan uzun bayroqlar
VALUE_SHORT = "bFtA"
# buyruqni o'rab oluvchilar: (qiymat oladigan optsiyalar, o'tkazib yuboriladigan pozitsion argumentlar soni)
WRAPPERS = {
    "env": ("-u -C --unset --chdir", 0), "command": ("", 0), "nohup": ("", 0), "builtin": ("", 0), "time": ("-f -o --format --output", 0),
    "sudo": ("-u -g -h -p -C -D -R -T -U --user --group --host --prompt --close-from --chdir --role --type --other-user --chroot --command-timeout", 0),
    "exec": ("-a", 0), "nice": ("-n --adjustment", 0), "timeout": ("-s -k --signal --kill-after", 1),
}
ANSI_C = re.compile(r"\$'((?:[^'\\]|\\.)*)'", re.S)
HEREDOC = re.compile(r"(?<!<)<<(?!<)(-?)\s*(?:'([^']+)'|\"([^\"]+)\"|\\?([A-Za-z_][A-Za-z0-9_]*))")
OPS = re.compile(r"\|\||&&|;;&|;;|;&|\|&|&>>|&>|>>|>\||>&|<<<|<<-|<<|<&|<>|[;&|()<>\n]")
OP_ONLY = re.compile(r"^[;&|()<>\n]+$")
REDIR_OPS = {">", ">>", ">|", ">&", "&>", "&>>", "<", "<<", "<<-", "<<<", "<&", "<>"}

SHELL_MSG = ("gh pr merge qobiq qatori ichida (bash/sh/zsh -c, eval, $(...), `...`, quvur orqali sh) qo'llab-quvvatlanmaydi: nishon PR ni ishonchli "
             "aniqlab bo'lmaydi. To'g'ridan-to'g'ri gh pr merge <N> --merge ni bajaring.")
QUOTE_MSG = "buyruq tahlil qilinmadi (tirnoqlar juftlanmagan?): PR raqamini oddiy ko'rinishda bering (masalan: gh pr merge 8 --merge)."


class Blocked(Exception):
    pass


def block(reason):
    raise Blocked(reason)


def one_line(text, limit=80):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 3] + "..."


class Cfg(object):
    def __init__(self, raw):
        self.prefix = raw["prefix"]
        self.gate = raw["gate_command"]
        self.reviewer = raw["reviewer"]


def load_config(explicit=None):
    """--config > $QUALITY_CONFIG > <hook papkasi>/../quality/config.json. Fayl yoki kalit yo'q = standart; buzuq fayl = standart + bitta stderr qator."""
    here = os.path.dirname(os.path.abspath(__file__))
    path = explicit or os.environ.get("QUALITY_CONFIG") or os.path.join(here, os.pardir, "quality", "config.json")
    data, problems = {}, []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as e:
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


def run(cmd, cwd=None, timeout=10):
    env = {k: v for k, v in os.environ.items() if k not in GIT_ENV_DROP}
    env.setdefault("GH_PROMPT_DISABLED", "1")
    try:
        return subprocess.run(cmd, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True, encoding="utf-8", errors="replace", timeout=timeout, env=env)
    except (OSError, subprocess.SubprocessError) as e:
        return subprocess.CompletedProcess(cmd, 127, "", str(e))


def git(d, *args):
    """(chiqish kodi, strip qilingan stdout)."""
    r = run(["git", "-C", d, *args])
    return r.returncode, r.stdout.strip()


def first_line(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.readline()
    except OSError:
        return None


# --- shell matnini bo'laklash (guard-git.py dan olingan usullar) ------------------------------------------------------------------------------

def strip_heredocs(cmd, bodies):
    """Heredoc tanasini tashlaydi (PR matnidagi `gh pr merge 7` buyruq emas). Tugatuvchi qator topilmasa tegmaydi.
    Tana bodies ga (matn, tirnoqsizmi) tushadi, `<<` yonidagi so'z __heredoc_N__ ga almashadi."""
    lines, out, i = cmd.split("\n"), [], 0
    absent = set()  # tugatuvchisi qolgan qatorlarda yo'q so'zlar: har `<<X` oxirigacha qayta qidirmasin (kvadratik sekinlashuv)
    while i < len(lines):
        line, kept, last = lines[i], [], 0
        i += 1
        for m in HEREDOC.finditer(line):
            word = m.group(2) or m.group(3) or m.group(4)
            key = (word, bool(m.group(1)))
            if key in absent:
                break
            j = i
            while j < len(lines) and (lines[j].lstrip("\t") if m.group(1) else lines[j]) != word:
                j += 1
            if j >= len(lines):
                absent.add(key)
                break
            kept.append(f"{line[last:m.start()]}<<__heredoc_{len(bodies)}__")
            last = m.end()
            bodies.append(("\n".join(lines[i:j]), bool(m.group(4)) and "\\" not in m.group(0)))
            i = j + 1
        out.append("".join(kept) + line[last:])
    return "\n".join(out)


def dollar_quotes(s):
    """Tirnoqdan tashqaridagi $'a\\nb' ni haqiqiy yangi qatorli 'a<qator>b' ga, $"a" ni "a" ga aylantiradi (shlex ularni bilmaydi)."""
    out, i, n, q = [], 0, len(s), None
    while i < n:
        c = s[i]
        if q is None and c == "$" and s[i + 1:i + 2] == "'":
            m = ANSI_C.match(s, i)
            if m:
                out.append("'" + m.group(1).replace("\\n", "\n").replace("\\t", "\t").replace("\\'", "'\\''") + "'")
                i = m.end()
                continue
        if q is None and c == "$" and s[i + 1:i + 2] == '"':
            i += 1
            continue
        if q == "'":
            if c == "'":
                q = None
        elif c == "\\" and i + 1 < n:
            out.append(c)
            i += 1
            c = s[i]
        elif q == '"':
            if c == '"':
                q = None
        elif c in "'\"":
            q = c
        out.append(c)
        i += 1
    return "".join(out)


def strip_comments(s):
    """Tirnoqdan tashqaridagi `# ...` izohni olib tashlaydi (shlex so'z o'rtasidagi # ni ham izoh deb o'qiydi).
    `#8` kabi yopishgan izoh so'zi `\\#8` bo'lib qoladi: `gh pr merge #8` da shell nishonni izoh deb tashlaydi, hook esa uni PR nomi sifatida bloklaydi."""
    out, i, n, q = [], 0, len(s), None
    while i < n:
        c = s[i]
        if q == "'":
            if c == "'":
                q = None
        elif c == "\\" and i + 1 < n:
            out.append(c)
            i += 1
            c = s[i]
        elif q == '"':
            if c == '"':
                q = None
        elif c in "'\"":
            q = c
        elif c == "#" and (i == 0 or s[i - 1] in " \t\n;&|()"):
            word = GLUED_COMMENT.match(s, i)
            if word:
                out.append("\\#" + word.group(1))
            while i < n and s[i] != "\n":
                i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def mark_fds(s):
    """Tirnoqdan tashqaridagi fd prefiksini (`2>&1` dagi 2, `3<file`) olib tashlaydi: shlex bo'shliqni yo'qotgani uchun `8 > out` bilan `8>out` ni
    keyin ajratib bo'lmaydi (PR raqami 8 redirekt prefiksi bo'lib qolmasin)."""
    out, i, n, q = [], 0, len(s), None
    while i < n:
        c = s[i]
        if q == "'":
            if c == "'":
                q = None
        elif c == "\\" and i + 1 < n:
            out.append(c)
            i += 1
            c = s[i]
        elif q == '"':
            if c == '"':
                q = None
        elif c in "'\"":
            q = c
        elif c.isdigit() and (i == 0 or s[i - 1] in " \t\n;&|()"):
            j = i
            while j < n and s[j].isdigit():
                j += 1
            if j < n and s[j] in "<>":
                i = j
                continue
        out.append(c)
        i += 1
    return "".join(out)


def substitutions(s, quotes=True):
    """Bajariladigan $(...) va `...` ichki satrlari (eng tashqi daraja); bitta tirnoq ichidagilar (markdown `kod`) bajarilmaydi.
    quotes=False: heredoc tanasi, u yerda tirnoq oddiy belgi."""
    out = []

    def walk(i, nested, sink):
        q, depth = None, 0
        while i < len(s):
            c = s[i]
            if q == "'":
                if c == "'":
                    q = None
            elif c == "\\":
                i += 1
            elif c == "`":
                j = i + 1
                while j < len(s) and s[j] != "`":
                    j += 2 if s[j] == "\\" else 1
                sink.append(s[i + 1:j])
                i = j
            elif c == "$" and s[i + 1:i + 2] == "(":
                j = walk(i + 2, True, [])
                sink.append(s[i + 2:j])
                i = j
            elif q == '"':
                if c == '"':
                    q = None
            elif quotes and c in "'\"":
                q = c
            elif nested and c == "(":
                depth += 1
            elif nested and c == ")":
                if depth == 0:
                    return i
                depth -= 1
            i += 1
        return len(s)

    walk(0, False, out)
    return out


def lex(s):
    # \n punktuatsiya: yangi qator buyruqlarni ajratadi; # ni izoh deb o'qimaydi (izohlar oldindan olib tashlangan)
    lx = shlex.shlex(s, posix=True, punctuation_chars=";&|()<>\n")
    lx.whitespace_split = True
    lx.whitespace = " \t\r"
    lx.commenters = ""
    return list(lx)


def strip_prefix(argv):
    """(haqiqiy buyruq boshlanadigan indeks, papkani almashtiradimi): VAR=val, shell kalit so'zlari va env/sudo/time/... o'ramlari (optsiyalari bilan)
    o'tkaziladi; `env -C DIR`, `sudo -D DIR` buyruqni boshqa papkada bajaradi, cd kuzatuvi buni bilmaydi."""
    i, chdir = 0, False
    while i < len(argv):
        a = argv[i]
        if ASSIGN.match(a) or a in SHELL_KEYWORDS:
            i += 1
            continue
        wrapper = os.path.basename(a)
        w = WRAPPERS.get(wrapper)
        if w is None:
            break
        valued, npos = w[0].split(), w[1]
        i += 1
        while i < len(argv) and argv[i].startswith("-"):
            if argv[i] == "--":
                i += 1
                break
            chdir = chdir or argv[i].startswith("--chdir") or (wrapper == "env" and argv[i] == "-C") or (wrapper == "sudo" and argv[i] == "-D")
            i += 2 if argv[i] in valued else 1
        i += npos
    return min(i, len(argv)), chdir


def shell_args(args):
    """Shell argumentlari: (-c matni yoki None, stdin skript sifatida bajariladimi; -n bajarmaydi, skript fayli stdin ni o'qimaydi)."""
    inline, forced, runs, i = False, False, True, 0
    while i < len(args) and args[i].startswith(("-", "+")) and args[i] not in ("-", "--"):
        a = args[i]
        i += 1
        if a.startswith("--"):
            if a in ("--rcfile", "--init-file"):
                i += 1
            inline = inline or a == "--command"
            continue
        inline = inline or "c" in a
        forced = forced or "s" in a
        runs = runs and "n" not in a
        if "o" in a or "O" in a:  # -o pipefail, -euo pipefail: qiymat skript emas
            i += 1
    if args[i:i + 1] == ["--"]:
        i += 1
    rest = args[i:]
    if inline:
        return (rest[0] if rest else None), False
    return None, runs and (forced or not rest or rest[0] in ("-", "/dev/stdin", "/dev/fd/0"))


# --- gh pr merge argumentlari -------------------------------------------------------------------------------------------------------------

def repo_flag(rest):
    """-R / --repo merge buyrug'ining istalgan joyida (qiymat sifatida ham), `-dR` kabi klasterda ham."""
    for tok in rest:
        if tok.startswith("--"):
            if tok.startswith("--repo"):
                return True
        elif tok.startswith("-"):
            for c in tok[1:]:
                if c == "R":
                    return True
                if c in VALUE_SHORT:
                    break
    return False


def find_target(rest):
    """Birinchi pozitsion argument indeksi (bayroqlar va ularning qiymatlari o'tkaziladi, `--` bayroqlarni tugatadi) yoki None."""
    skip = posonly = False
    for k, tok in enumerate(rest):
        if skip:
            skip = False
            continue
        if posonly or not tok.startswith("-"):
            return k
        if tok == "--":
            posonly = True
        elif tok.startswith("--"):
            skip = tok in VALUE_LONG
        else:
            flags = tok[1:]
            for n, c in enumerate(flags):
                if c in VALUE_SHORT:
                    skip = n == len(flags) - 1  # klasterning oxirgisi bo'lsa qiymat keyingi argumentda (-b MATN), aks holda yopishgan (-bMATN)
                    break
    return None


def takes_value(tok):
    """gh ning -R/--repo bayrog'i qiymatni keyingi argumentda oladimi: `--repo`, `-R`, `-hR`; `--repo=O/R` va `-RO/R` bitta so'z."""
    if tok == "--repo":
        return True
    if tok.startswith("-") and not tok.startswith("--"):
        k = tok.find("R")
        return k != -1 and k == len(tok) - 1
    return False


def merge_index(args):
    """`gh [bayroqlar] pr [bayroqlar] merge` da `merge` ning args (gh dan keyingi so'zlar) dagi indeksi, aks holda None.
    Bayroqlar: -R/--repo qiymati bilan, --help kabi; `--` bayroqlarni tugatadi (cobra shunda `pr` ni o'zini bajaradi)."""
    i = 0
    for word in ("pr", "merge"):
        while i < len(args) and args[i].startswith("-") and args[i] not in ("-", "--"):
            i += 2 if takes_value(args[i]) else 1
        if i >= len(args) or args[i] != word:
            return None
        i += 1
    return i - 1


def python_target(args):
    """python optsiyalaridan keyingi nishon: (skript yo'li yoki -m moduli nomi, uning argumentlari). Nishon yo'q yoki -c (kod matni) = (None, []).
    -m/-W/-X qiymat oladi (keyingi argumentda yoki yopishgan), -I -B -u kabilar olmaydi."""
    i, n = 0, len(args)
    while i < n:
        a, i = args[i], i + 1
        if a == "--":
            return (args[i], args[i + 1:]) if i < n else (None, [])
        if a == "-" or not a.startswith("-"):
            return a, args[i:]
        if a.startswith("--"):
            i += a == "--check-hash-based-pycs"
            continue
        for k in range(1, len(a)):
            c = a[k]
            if c == "c":
                return None, []
            if c in "mWX":
                value = a[k + 1:]
                if not value:
                    value, i = (args[i], i + 1) if i < n else ("", i)
                if c == "m":
                    return value.rsplit(".", 1)[-1], args[i:]
                break
    return None, []


def script_call(cmd):
    """(iz skripti nomi, uning argumentlari): cmd[0] ning o'zi skript (har qanday yo'l prefiksi bilan) yoki python/python3/python3.x ning skript (-m: modul)
    argumenti. Skript boshqa dastur argumenti bo'lsa (grep, cat, echo, git ...) yoki cmd boshqa narsani ishga tushirsa (None, [])."""
    if PYTHON.fullmatch(os.path.basename(cmd[0])):
        word, rest = python_target(cmd[1:])
    else:
        word, rest = cmd[0], cmd[1:]
    m = STAMP_SCRIPT.fullmatch(os.path.basename(word)) if word else None
    return (m.group(1), rest) if m else (None, [])


def is_write_flag(tok, script):
    """Skriptning izni yozadigan bayrog'i va argparse qisqartmalari: review_stamp.py da --head/--report (--hea, --rep), gate_stamp.py da --write (--wr)."""
    if not tok.startswith("--"):
        return False
    names, shortest = WRITE_FLAGS[script]
    name = tok[2:].split("=", 1)[0]
    return len(name) >= shortest and any(full.startswith(name) for full in names)


class Ctx(object):
    """Bitta hook chaqiruvining umumiy holati: hodisa, sozlama (kerak bo'lganda yuklanadi), repo tekshiruvlari keshi."""

    def __init__(self, event, raw, config_arg=None):
        self.raw = raw
        cwd, agent = event.get("cwd"), event.get("agent_type")
        self.cwd = cwd if isinstance(cwd, str) and cwd else None
        self.agent = agent if isinstance(agent, str) else None
        self.config_arg = config_arg
        self._cfg = None
        self._base = None
        self.heredocs = []
        self.checked = set()

    @property
    def cfg(self):
        if self._cfg is None:
            self._cfg = load_config(self.config_arg)
        return self._cfg

    def base_dir(self):
        """Hodisadagi cwd (repo ichida bo'lsa), aks holda $CLAUDE_PROJECT_DIR: worktree da iz o'z git papkasida, CLAUDE_PROJECT_DIR esa asosiy checkout."""
        if self._base is None:
            d = self.cwd
            if not d or git(d, "rev-parse", "--git-dir")[0] != 0:
                d = os.environ.get("CLAUDE_PROJECT_DIR") or "."
            self._base = os.path.abspath(d)
        return self._base

    def stamp_file(self, d, name):
        rc, out = git(d, "rev-parse", "--git-path", name)
        if rc != 0 or not out:
            block(f"git papkasi aniqlanmadi ({d} repo emas?).")
        return os.path.join(d, out)

    def check_pr(self, d, pr):
        """d papkadagi repo da PR (pr = "" bo'lsa joriy branch PR i) uchun darvoza izi va ko'rik izini tekshiradi; yaroqsiz bo'lsa Blocked."""
        if (d, pr) in self.checked:
            return
        cfg = self.cfg
        gate, reviewer = cfg.gate, cfg.reviewer
        r = run(["gh", "pr", "view", *([pr] if pr else []), "--json", "number,headRefOid"], cwd=d, timeout=25)
        head = num = None
        if r.returncode == 0:
            try:
                info = json.loads(r.stdout)
                head, num = info.get("headRefOid"), info.get("number")
            except (ValueError, AttributeError):
                pass
        if not (isinstance(head, str) and OID.fullmatch(head.lower()) and isinstance(num, int) and not isinstance(num, bool) and num > 0):
            block(f"PR head commit i aniqlanmadi (gh pr view{' ' + pr if pr else ''}).")
        head, prnum = head.lower(), str(num)
        rc, tree = git(d, "rev-parse", "--verify", "--quiet", head + "^{tree}")
        if rc != 0 or not tree:
            block(f"PR head {head} lokal repoda yo'q: git fetch, keyin {gate}.")

        ci_file = self.stamp_file(d, f"{cfg.prefix}-ci-ok")
        fields = (first_line(ci_file) or "").split()
        if not fields:
            block(f"{gate} yashil natijasi yo'q ({ci_file}). PR branchida {gate} ni ishga tushiring.")
        if fields[0] != tree:
            block(f"{gate} boshqa kod uchun yashil bo'lgan (iz {fields[0]}, PR head {head} daraxti {tree}). "
                  f"PR branchida, hammasi commit va push qilingach, {gate} ni qayta ishga tushiring.")

        review_file = self.stamp_file(d, f"{cfg.prefix}-review-ok-{prnum}")
        hint = f"{reviewer} agenti PR #{prnum} ning head {head} ini ko'rib chiqishi va review_stamp.py bilan izni yozishi kerak."
        f = (first_line(review_file) or "").split()
        if len(f) < 3:
            block(f"mustaqil ko'rik izi yo'q yoki buzuq ({review_file}). {hint}")
        r_tree, r_pr, r_head, policy = f[0], f[1], f[2], {}
        for tok in f[4:]:
            for key in ("round", "blockers", "major"):
                if tok.startswith(key + "="):
                    policy[key] = tok[len(key) + 1:]
        if not all(re.fullmatch(r"[0-9]+", policy.get(key, "")) for key in ("round", "blockers", "major")):
            block(f"mustaqil ko'rik izi yo'q yoki buzuq: eski shakl, round/blockers/major yo'q ({review_file}). {hint}")
        r_round, r_blockers, r_major = int(policy["round"]), int(policy["blockers"]), int(policy["major"])
        if r_pr != prnum:
            block(f"mustaqil ko'rik izi boshqa PR uchun (iz #{r_pr}, merge #{prnum}). {hint}")
        if r_blockers != 0:
            block(f"mustaqil ko'rik izida bloker bor (blockers={r_blockers}, {r_round}-tur): blokerlar tuzatilib qayta ko'rilishi kerak. {hint}")
        if r_round == 1 and r_major != 0:
            block(f"1-ko'rikda muhim topilma bor (major={r_major}): tuzatish va 2-ko'rik shart. {hint}")
        if r_tree != tree and not (OID.fullmatch(r_head) and git(d, "merge-base", "--is-ancestor", r_head, head)[0] == 0):
            block(f"mustaqil ko'rik eskirgan: iz boshqa kod uchun (iz head {r_head} joriy head {head} ning ajdodi emas; "
                  f"iz daraxti {r_tree}, PR head daraxti {tree}). {hint}")
        self.checked.add((d, pr))


class Scan(object):
    """Bitta buyruq matnini tahlil qiladi. inner=True: ichki satr ($(...), -c matni, eval): cd kuzatilmaydi, undagi har merge bloklanadi."""

    def __init__(self, ctx, depth=0, inner=False):
        self.ctx = ctx
        self.depth = depth
        self.inner = inner
        self.dir = None  # literal cd lardan keyingi papka (None = hali cd yo'q: base_dir)
        self.unknown = None  # cd nishoni aniqlanmagan bo'lsa sabab; merge shu holatda bloklanadi
        self.stack = []  # ( ... ) kichik qobiq: ichidagi cd tashqariga chiqmaydi

    def fork(self):
        return Scan(self.ctx, self.depth + 1, True)

    def cur(self):
        return self.dir if self.dir is not None else self.ctx.base_dir()

    def run(self, cmd):
        ctx = self.ctx
        if self.depth > MAX_DEPTH:
            if MERGE_TEXT.search(ctx.raw):
                block("buyruq juda chuqur ichma-ich (bash -c / eval / $()): tahlil qilib bo'lmaydi.")
            return
        start = len(ctx.heredocs)
        text = dollar_quotes(strip_comments(strip_heredocs(cmd, ctx.heredocs)).replace("\\\n", ""))
        inners = substitutions(text)
        bodies = [s for body, expands in ctx.heredocs[start:] if expands for s in substitutions(body, quotes=False)]
        for inner in inners + bodies:
            self.fork().run(inner)
        for inner in inners:  # bitta so'z bo'lib qoladi: `gh pr merge $(x)` da shlex qavslarda buyruqni bo'lmasin
            text = text.replace("$(" + inner + ")", "$SUBST").replace("`" + inner + "`", "$SUBST")
        try:
            tokens = lex(mark_fds(text))
        except ValueError:  # tirnoq juftlanmagan: xom matn bo'yicha konservativ tekshiruv
            if MERGE_TEXT.search(ctx.raw):
                block(QUOTE_MSG)
            if STAMP_WORD.search(text) and WRITE_FLAG_TEXT.search(text):
                self.stamp_denied()
            if GATE_WORD.search(text) and GATE_FLAG_TEXT.search(text):
                self.gate_denied()
            return
        self.walk(tokens)

    def walk(self, tokens):
        argv, redirs, pending, piped = [], [], None, False
        elem_start = (self.dir, self.unknown)
        for tok in tokens:
            if not OP_ONLY.match(tok):
                if pending:
                    redirs.append((pending[0], tok, pending[1]))
                    pending = None
                else:
                    argv.append(tok)
                continue
            for p in OPS.findall(tok):
                if p in REDIR_OPS:
                    pending = (p, len(argv))
                    continue
                if argv or redirs:
                    self.simple(argv, redirs)
                argv, redirs, pending = [], [], None
                if p == "(":
                    self.stack.append((self.dir, self.unknown))
                elif p == ")" and self.stack:
                    self.dir, self.unknown = self.stack.pop()
                elif piped or p in ("|", "|&", "&"):
                    self.dir, self.unknown = elem_start  # quvur elementi yoki fon ishidagi cd keyingi buyruqqa o'tmaydi
                piped = p in ("|", "|&")
                if not piped:
                    elem_start = (self.dir, self.unknown)
        if argv or redirs:
            self.simple(argv, redirs)

    def simple(self, argv, redirs):
        start, chdir = strip_prefix(argv)
        cmd = argv[start:]
        if not cmd:
            return
        name, args = os.path.basename(cmd[0]), cmd[1:]
        at = merge_index(args) if name == "gh" else None
        if name in ("cd", "pushd"):
            self.cd(args)
        elif name == "popd":
            self.unknown = "popd dan keyin papka noma'lum: gh pr merge ni qaysi repo da bajarish aniq emas. To'liq yo'l bilan cd qiling."
        elif at is not None:
            if self.inner:
                block(SHELL_MSG)
            if chdir:
                block("env -C / sudo -D bilan merge qo'llab-quvvatlanmaydi: gh pr merge ni qaysi repo da bajarish noma'lum. Avval cd <to'liq yo'l> qiling.")
            self.merge(argv, start, start + 1 + at, redirs)
        elif name in SHELLS:
            inline, reads_stdin = shell_args(args)
            if inline is not None:
                self.fork().run(inline)
            if reads_stdin and MERGE_TEXT.search(self.ctx.raw):  # echo '...' | sh, sh <<< '...': matn shell ga stdin orqali boradi
                block(SHELL_MSG)
        elif name == "eval":
            self.fork().run(" ".join(args))
        script, rest = script_call(cmd)
        if script and any(is_write_flag(w, script) for w in rest):
            if script == "review_stamp":
                self.stamp_denied()
            else:
                self.gate_denied()

    def stamp_denied(self):
        reviewer = self.ctx.cfg.reviewer
        if self.ctx.agent != reviewer:
            block(f"ko'rik izini faqat {reviewer} agenti yozadi (review_stamp.py --head/--report): bu chaqiruv shu agent ichida emas. "
                  f"Ko'rikni {reviewer} agentiga topshiring (--check va --allow shakllari hammaga ochiq).")

    def gate_denied(self):
        gate = self.ctx.cfg.gate
        block(f"darvoza izini faqat darvoza skripti yozadi ({gate} ichida), qo'lda emas (gate_stamp.py --write). "
              f"Izni {gate} yozadi: uni ishga tushiring (--tree, --check, --clear shakllari ochiq).")

    def cd(self, args):
        if self.inner:
            return
        rest, flags = [], True
        for a in args:
            if flags and a == "--":
                flags = False
            elif not (flags and CD_FLAGS.match(a)):
                rest.append(a)
        target = rest[0] if rest else "~"
        if target == "-" or "$" in target or "`" in target:
            self.unknown = f"cd nishoni ({one_line(target)}) aniqlanmadi: gh pr merge ni qaysi repo da bajarish noma'lum. To'liq yo'l bilan cd qiling."
            return
        path = os.path.expanduser(target)
        if os.path.isabs(path):
            self.unknown = None
        elif self.unknown:
            return  # noma'lum papkadan nisbiy yo'l: noma'lum qoladi
        new = os.path.normpath(os.path.join(self.cur(), path))
        if os.path.isdir(new):
            self.dir = new
        else:
            self.unknown = f"cd {one_line(target)}: papka yo'q."

    def merge(self, argv, start, at, redirs):
        """`gh pr merge` buyrug'i: argv[start] = gh, argv[at] = merge (ular orasida faqat `pr` va bayroqlar).
        Qoidalar va tartib: cd aniqligi, -R/--repo, GH_REPO/GH_HOST, nishon, so'ng izlar."""
        if self.unknown:
            block(self.unknown)
        first = at + 1  # argv dagi `merge` dan keyingi birinchi indeks
        rest = argv[first:]
        if repo_flag(argv[start + 1:at] + rest):  # `gh pr --repo o/r merge 5` ham
            block("gh pr merge -R/--repo qo'llab-quvvatlanmaydi (iz qaysi repo ga tegishli ekani noma'lum): repo papkasiga o'tib (masalan cd <submodule>) -R siz bajaring.")
        if GH_ENV.search(self.ctx.raw):  # butun buyruq bo'yicha: oldingi bo'lakdagi `export GH_REPO=...` ham
            block("GH_REPO/GH_HOST bilan merge qo'llab-quvvatlanmaydi (iz qaysi repo ga tegishli ekani noma'lum): repo papkasiga o'tib, bu o'zgaruvchilarsiz bajaring.")
        k = find_target(rest)
        end = len(argv) if k is None else first + k
        for op, word, pos in redirs:
            if first <= pos <= end:  # `gh pr merge >out 8`: redirekt nishondan oldin, nishon noaniq
                block(f"merge nishoni ({one_line(op + word)}) oddiy PR raqami emas: redirekt merge dan keyin, nishondan oldin. PR raqamini oddiy ko'rinishda bering (masalan: 8 --merge).")
        pr = ""
        if k is not None:
            target = rest[k]
            if not re.fullmatch(r"[0-9]+", target):
                block(f"merge nishoni ({one_line(target)}) oddiy PR raqami emas (URL, branch nomi, #N qo'llab-quvvatlanmaydi: iz qaysi PR ga tegishli ekani "
                      "noma'lum). PR raqamini o'zi bilan bering (masalan: 8 --merge).")
            pr = target.lstrip("0")
            if not pr:
                block(f"merge nishoni ({one_line(target)}) PR raqami emas.")
        self.ctx.check_pr(self.cur(), pr)


def main():
    for stream, errors in ((sys.stdin, "replace"), (sys.stderr, "backslashreplace")):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors=errors)
    ap = argparse.ArgumentParser(description="PreToolUse (Bash) hook: gh pr merge faqat yaroqli izlar bilan.")
    ap.add_argument("--config", help="standart: $QUALITY_CONFIG, yo'q bo'lsa <hook papkasi>/../quality/config.json")
    args, _ = ap.parse_known_args()  # begona argument hook ni bloklamasin (exit 2 = blok)
    try:
        event = json.load(sys.stdin)
        cmd = event["tool_input"]["command"]
        if event.get("tool_name") not in (None, "Bash") or not isinstance(cmd, str) or not cmd:
            return 0
    except Exception:
        return 0
    if not (GH_WORD.search(cmd) or "review_stamp" in cmd or "gate_stamp" in cmd):
        return 0
    ctx = Ctx(event, cmd, args.config)
    try:
        Scan(ctx).run(cmd)
    except Blocked as e:
        print(f"Merge bloklandi: {e} {RULE}", file=sys.stderr)
        return 2
    except RecursionError:  # juda chuqur ichma-ich qavslar: merge matni bo'lsa yopiq
        if MERGE_TEXT.search(cmd):
            print(f"Merge bloklandi: buyruq juda chuqur ichma-ich, tahlil qilib bo'lmadi. {RULE}", file=sys.stderr)
            return 2
    except Exception:
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
