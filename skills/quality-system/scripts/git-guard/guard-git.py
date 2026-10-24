#!/usr/bin/env python3
"""PreToolUse (Bash): himoyalangan branch (main, master) ga PR siz yetib borishning git/gh yo'llarini yopadi.

Kirish: stdin JSON {tool_input: {command}, cwd}. Bloklash: exit 2 va sabab stderr ga; ruxsat: exit 0.
Buyruq shell kabi bo'laklanadi (heredoc tanasi va izohlar tashlanadi), shuning uchun faqat haqiqiy buyruqlar
tekshiriladi: echo "git push --force" yoki PR matni ichidagi so'z qoidaga tushmaydi.
Kutilmagan xato bo'lsa exit 1 (bloklamaydi): himoya vositasi ishni butunlay to'xtatib qo'ymasin.
Himoyalangan branchlar: .claude/quality/config.json "protected" (repo ildizidan, keyin $CLAUDE_PROJECT_DIR dan), standart main va master.
"""
import json
import os
import re
import shlex
import subprocess
import sys
import traceback

DEFAULT_PROTECTED = ("main", "master")
CONFIG_REL = os.path.join(".claude", "quality", "config.json")
# make ci / ko'rik izi: `<prefiks>-ci-ok`, `<prefiks>-review-ok` yoki prefikssiz `ci-ok`, `review-ok`
STAMP_CORE = r"(?:[A-Za-z0-9_]+-)?(?:ci|review)-ok(?![A-Za-z0-9_])"
STAMP = re.compile(STAMP_CORE)
STAMP_MSG = "tekshiruv izini (<prefiks>-ci-ok, <prefiks>-review-ok) qo'lda yozib bo'lmaydi"
ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
STAMP_SUBST_TARGET = re.compile(r">\|?\s*\S*(?:\$\(|`)[^)`\n]*" + STAMP_CORE)
HOOKS_ENV = re.compile(r"GIT_CONFIG\w*=\S*(?:hookspath|quality\.protected)", re.I)
MAX_DEPTH = 8

# faqat o'qiydigan buyruqlar: argumentida iz nomi bo'lsa ham bloklanmaydi (echo/printf/gh: PR matni; yozish baribir > yoki tee orqali ushlanadi)
READ_ONLY = {"cat", "head", "tail", "less", "ls", "stat", "test", "[", "wc", "grep", "rg", "diff", "cut", "file", "git",
             "echo", "printf", "gh"}
SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "fish"}
# buyruqni o'rab oluvchilar: (qiymat oladigan optsiyalar, o'tkazib yuboriladigan pozitsion argumentlar soni)
WRAPPERS = {
    "env": ("-u -C -S", 0), "command": ("", 0), "nohup": ("", 0), "time": ("-f -o", 0), "sudo": ("-u -g -h -p -C -D -R -T -U", 0),
    "exec": ("-a", 0), "builtin": ("", 0), "nice": ("-n", 0), "timeout": ("-s -k", 1),
}
# git uzun optsiyalarning qisqartmasini ham qabul qiladi (--no-ver), shuning uchun prefiks bo'yicha tekshiriladi
PUSH_DANGER = {
    "--force": "force push", "--force-with-lease": "force push", "--force-if-includes": "force push",
    "--mirror": "--mirror hamma refni ustiga yozadi", "--all": "--all hamma branchni yuboradi",
    "--branches": "--branches hamma branchni yuboradi", "--no-verify": "--no-verify pre-push hook ni o'chiradi",
}
PUSH_VALUE_OPTS = {"--repo", "--receive-pack", "--exec", "--push-option", "--recurse-submodules"}
GH_API_VALUE_OPTS = {"-H", "--header", "-q", "--jq", "-t", "--template", "--hostname", "--cache", "-p", "--preview"}
GH_API_PATH = re.compile(r"git/refs|/merges|/contents/|pulls/\d+/merge")
GRAPHQL_DANGER = re.compile(r"mergePullRequest|createCommitOnBranch|updateRef|createRef|deleteRef|enablePullRequestAutoMerge")
ALLOWED_HOOKS_PATH = re.compile(r"^(\.\./)*\.githooks$")  # .githooks, submodule da ../../.githooks, ichma-ich da ../../../.githooks
GUARDED_KEYS = ("core.hookspath", "quality.protected")  # hook yo'lini va himoyalangan branch ro'yxatini jimgina o'zgartirib bo'lmaydi

HEREDOC = re.compile(r"(?<!<)<<(?!<)(-?)\s*(?:'([^']+)'|\"([^\"]+)\"|\\?([A-Za-z_][A-Za-z0-9_]*))")
OPS = re.compile(r"\|\||&&|;;&|;;|;&|\|&|&>>|&>|>>|>\||>&|<<<|<<-|<<|<&|<>|[;&|()<>\n]")
OP_ONLY = re.compile(r"^[;&|()<>\n]+$")
REDIR_OPS = {">", ">>", ">|", ">&", "&>", "&>>", "<", "<<", "<<-", "<<<", "<&", "<>"}
GIT_ENV_DROP = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_PREFIX", "GIT_COMMON_DIR")


class Blocked(Exception):
    pass


def block(reason):
    raise Blocked(reason)


def git_out(d, extra, *args):
    """git -C d <extra> <args> chiqishi; xato bo'lsa None (repo emas, aniqlab bo'lmadi)."""
    env = {k: v for k, v in os.environ.items() if k not in GIT_ENV_DROP}
    try:
        r = subprocess.run(["git", "-C", d, *extra, *args], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                           timeout=5, env=env)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def read_protected(roots):
    """config.json dagi "protected" (birinchi topilgan ildizdan); o'qib bo'lmasa yoki bo'sh/noto'g'ri bo'lsa standart (fail closed)."""
    for root in roots:
        if not root:
            continue
        try:
            with open(os.path.join(root, CONFIG_REL), encoding="utf-8") as f:
                value = json.load(f).get("protected")
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(value, list) and value and all(isinstance(b, str) and b for b in value):
            return tuple(value)
    return DEFAULT_PROTECTED


def strip_heredocs(cmd):
    """Heredoc tanasini tashlaydi (PR matnidagi `git push origin main` buyruq emas). Tugatuvchi qator topilmasa tegmaydi."""
    lines, out, i = cmd.split("\n"), [], 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        i += 1
        for m in HEREDOC.finditer(line):
            word = m.group(2) or m.group(3) or m.group(4)
            j = i
            while j < len(lines) and (lines[j].lstrip("\t") if m.group(1) else lines[j]) != word:
                j += 1
            if j >= len(lines):
                break
            i = j + 1
    return "\n".join(out)


def strip_comments(s):
    """Tirnoqdan tashqaridagi `# ...` izohni olib tashlaydi (shlex so'z o'rtasidagi # ni ham izoh deb o'qiydi)."""
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
            while i < n and s[i] != "\n":
                i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def substitutions(s):
    """Bajariladigan $(...) va `...` ichki satrlari; bitta tirnoq ichidagilar (masalan markdown `kod`) bajarilmaydi."""
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
            elif c in "'\"":
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
    """VAR=val, env/sudo/time/... o'ramlarini olib tashlab, haqiqiy buyruqni qaytaradi."""
    i = 0
    while i < len(argv):
        a = argv[i]
        if ASSIGN.match(a):
            i += 1
            continue
        w = WRAPPERS.get(os.path.basename(a))
        if w is None:
            break
        valued, npos = w[0].split(), w[1]
        i += 1
        while i < len(argv) and argv[i].startswith("-"):
            if argv[i] == "--":
                i += 1
                break
            i += 2 if argv[i] in valued else 1
        i += npos
    return argv[i:]


def read_only(name, args):
    if name == "sed":
        return not any(a.startswith("--in-place") or (a.startswith("-") and not a.startswith("--") and "i" in a) for a in args)
    return name in READ_ONLY


def hooks_key(key):
    name = key.split("=", 1)[0].strip().lower()
    if name == "core.hookspath":
        block("`-c core.hooksPath` git hook larni o'chiradi")
    if name == "quality.protected":
        block("`-c quality.protected` pre-push hook ning himoyalangan branch ro'yxatini o'zgartiradi")


def fallback(s, protected):
    """shlex o'qiy olmasa (tirnoq juftlanmagan): xom matn bo'yicha konservativ tekshiruv."""
    names = "|".join(re.escape(b) for b in protected)
    if re.search(r"\bgit\b[^\n]*\bpush\b", s) and re.search(
            r"(?<!\S)(--force\S*|-[A-Za-z]*f[A-Za-z]*|\+\S+|--no-verify|--mirror|--all)(?!\S)|\b(" + names + r")\b", s):
        block("git push (buyruqni o'qib bo'lmadi, tirnoqlar juftlanmagan): force, +ref, --no-verify yoki himoyalangan branch bor")
    if re.search(r">\s*\S*" + STAMP_CORE, s):
        block(STAMP_MSG)


class Guard:
    def __init__(self, cwd, cache=None, depth=0):
        self.cwd = cwd
        self.depth = depth
        self.cache = {} if cache is None else cache
        self.vbranch = {}  # repo ildizi -> shu buyruq ichidagi checkout/switch dan keyingi branch (None = detached)

    def fork(self):
        g = Guard(self.cwd, self.cache, self.depth + 1)
        g.vbranch = dict(self.vbranch)
        return g

    # --- repo holati -------------------------------------------------------------------------------------------
    def top(self, d, extra):
        k = ("top", d, extra)
        if k not in self.cache:
            self.cache[k] = git_out(d, extra, "rev-parse", "--show-toplevel")
        return self.cache[k] or d

    def branch(self, d, extra):
        top = self.top(d, extra)
        if top in self.vbranch:
            return self.vbranch[top]
        k = ("branch", d, extra)
        if k not in self.cache:
            self.cache[k] = git_out(d, extra, "symbolic-ref", "--short", "-q", "HEAD") or None
        return self.cache[k]

    def protected(self, d, extra):
        """Shu repo uchun himoyalangan branchlar: avval repo ildizidagi config, keyin $CLAUDE_PROJECT_DIR dagi."""
        top = self.top(d, extra)
        k = ("protected", top)
        if k not in self.cache:
            self.cache[k] = read_protected([top, os.environ.get("CLAUDE_PROJECT_DIR")])
        return self.cache[k]

    def join(self, d, p):
        return os.path.normpath(os.path.join(d, os.path.expanduser(p)))

    # --- tahlil ------------------------------------------------------------------------------------------------
    def run(self, cmd):
        if self.depth > MAX_DEPTH:
            block("buyruq juda chuqur ichma-ich (bash -c / eval / $())")
        cmd = strip_comments(strip_heredocs(cmd)).replace("\\\n", "")
        if STAMP_SUBST_TARGET.search(cmd):  # > $(git rev-parse --git-path x-ci-ok): shlex da nishon faqat `$` bo'lib qoladi
            block(STAMP_MSG)
        for inner in substitutions(cmd):
            self.fork().run(inner)
        try:
            tokens = lex(cmd)
        except ValueError:
            fallback(cmd, self.protected(self.cwd, ()))
            return
        self.walk(tokens)

    def walk(self, tokens):
        argv, redirs, pending, stack = [], [], None, []
        for tok in tokens:
            if not OP_ONLY.match(tok):
                if pending:
                    redirs.append((pending, tok))
                    pending = None
                else:
                    argv.append(tok)
                continue
            for p in OPS.findall(tok):
                if p in REDIR_OPS:
                    if argv and argv[-1].isdigit() and not pending:
                        argv.pop()  # fd prefiksi: 2>file
                    pending = p
                    continue
                if argv or redirs:
                    self.simple(argv, redirs)
                argv, redirs, pending = [], [], None
                if p == "(":
                    stack.append(self.cwd)
                elif p == ")" and stack:
                    self.cwd = stack.pop()
        if argv or redirs:
            self.simple(argv, redirs)

    def simple(self, argv, redirs):
        for op, target in redirs:
            if ">" in op and STAMP.search(target):
                block(STAMP_MSG)
        if any(HOOKS_ENV.search(w) for w in argv):
            block("GIT_CONFIG_* orqali core.hooksPath ni o'zgartirib bo'lmaydi")
        cmd = strip_prefix(argv)
        if not cmd:
            return
        name, args = os.path.basename(cmd[0]), cmd[1:]
        if name in ("cd", "pushd"):
            self.cd(args)
        elif name == "git":
            self.git(args)
        elif name == "gh":
            self.gh(args)
        elif name in SHELLS:
            for i, a in enumerate(args):
                if a == "--":
                    break
                if a.startswith("-") and not a.startswith("--") and "c" in a[1:] and i + 1 < len(args):
                    self.fork().run(args[i + 1])
                    break
        elif name == "eval":
            self.fork().run(" ".join(args))
        if any(STAMP.search(a) for a in args) and not read_only(name, args):
            block("tekshiruv izini (<prefiks>-ci-ok, <prefiks>-review-ok) qo'lda yozib yoki o'zgartirib bo'lmaydi")

    def cd(self, args):
        rest = [a for a in args if a not in ("-P", "-L", "-e", "-@", "--")]
        t = rest[0] if rest else "~"
        t = os.path.expandvars(os.path.expanduser(t))
        if t == "-" or "$" in t or "`" in t:
            return  # qayerga borishi noma'lum: joriy papka qoladi
        self.cwd = self.join(self.cwd, t)

    # --- git ---------------------------------------------------------------------------------------------------
    def git(self, args):
        d, extra, i = self.cwd, [], 0
        while i < len(args):
            a = args[i]
            nxt = args[i + 1] if i + 1 < len(args) else ""
            if a == "-C":
                d = self.join(d, nxt)
                i += 2
            elif a == "-c":
                hooks_key(nxt)
                i += 2
            elif a.startswith("--config-env="):
                hooks_key(a.split("=", 1)[1])
                i += 1
            elif a == "--config-env":
                hooks_key(nxt)
                i += 2
            elif a in ("--git-dir", "--work-tree"):
                extra += [a, nxt]
                i += 2
            elif a.startswith(("--git-dir=", "--work-tree=")):
                extra.append(a)
                i += 1
            elif a in ("--namespace", "--super-prefix"):
                i += 2
            elif a.startswith("-"):
                i += 1
            else:
                break
        if i >= len(args):
            return
        sub, rest, extra = args[i], args[i + 1:], tuple(extra)
        if sub == "push":
            self.push(rest, d, extra)
        elif sub in ("commit", "cherry-pick", "revert", "am", "merge"):
            b = self.branch(d, extra)
            if b in self.protected(d, extra) and not (sub == "merge" and "--ff-only" in rest) and not any(a in ("--abort", "--quit") for a in rest):
                block(f"{b} branchida `git {sub}` taqiqlangan (avval feature branch oching)")
        elif sub == "config":
            self.config(rest)
        elif sub in ("checkout", "switch"):
            self.switch(sub, rest, d, extra)

    def push(self, rest, d, extra):
        remote, specs, tags, done, i = None, [], False, False, 0
        while i < len(rest):
            a = rest[i]
            i += 1
            if done or not a.startswith("-") or a == "-":
                if remote is None:
                    remote = a
                else:
                    specs.append(a)
            elif a == "--":
                done = True
            elif a.startswith("--"):
                name, eq, _ = a.partition("=")
                for full, why in PUSH_DANGER.items():
                    if len(name) >= 3 and full.startswith(name):
                        block(f"git push {name}: {why}")
                tags = tags or name == "--tags"
                if name in PUSH_VALUE_OPTS and not eq:
                    i += 1
            else:
                for k, ch in enumerate(a[1:]):
                    if ch == "f":
                        block("git push -f: force push")
                    if ch == "o":  # -o <qiymat>: qiymatdagi harflar bayroq emas
                        i += 1 if k == len(a) - 2 else 0
                        break
        prot = self.protected(d, extra)
        if not specs:
            b = self.branch(d, extra)
            if not tags and b in prot:
                block(f"joriy branch {b}: refspec siz git push {b} ga ketadi")
            return
        for s in specs:
            if s.startswith("+"):
                block(f"git push {s}: +refspec force push")
            src, _, dst = s.partition(":")
            t = dst or src  # `main:` ham himoyalanadi
            for pre in ("refs/heads/", "heads/"):
                if t.startswith(pre):
                    t = t[len(pre):]
                    break
            if t in ("HEAD", "@"):
                t = self.branch(d, extra) or ""
            if "*" in t:
                block(f"git push {s}: glob refspec himoyalangan branchni ham yuboradi")
            if t in prot:
                block(f"{t} ga to'g'ridan-to'g'ri push taqiqlangan")

    def switch(self, sub, rest, d, extra):
        new, detach, pos, i = None, False, [], 0
        while i < len(rest):
            a = rest[i]
            i += 1
            if a == "--":
                return  # fayl yo'llari: branch almashmaydi
            if a in ("-b", "-B", "-c", "-C") and i < len(rest):
                new = rest[i]
                i += 1
            elif a in ("-d", "--detach"):
                detach = True
            elif a.startswith("-") and a != "-":
                continue
            else:
                pos.append(a)
        top = self.top(d, extra)
        if new:
            self.vbranch[top] = new
        elif detach:
            self.vbranch[top] = None
        elif pos:
            t = pos[0]
            if t == "-":
                t = git_out(d, extra, "rev-parse", "--abbrev-ref", "@{-1}")
            # checkout X fayl ham bo'lishi mumkin: faqat mavjud branch yoki himoyalangan nom bo'lsa almashadi
            if t and (sub == "switch" or t in self.protected(d, extra) or git_out(d, extra, "show-ref", "--verify", "--quiet", "refs/heads/" + t) is not None):
                self.vbranch[top] = t

    def config(self, rest):
        pos, flags, i = [], [], 0
        while i < len(rest):
            a = rest[i]
            i += 1
            if a in ("-f", "--file", "--blob", "--type", "--default"):
                i += 1
            elif a.startswith("-"):
                flags.append(a)
            else:
                pos.append(a)
        mode = pos.pop(0) if pos and pos[0] in ("get", "set", "unset", "list", "edit") else None  # git 2.46+ sintaksisi
        keys = [k for k, p in enumerate(pos) if p.lower() in GUARDED_KEYS]
        if not keys:
            return
        key = pos[keys[0]].lower()
        if mode == "unset" or any(f.startswith("--unset") or f == "--remove-section" for f in flags):
            block(f"{pos[keys[0]]} ni o'chirib bo'lmaydi")
        reading = mode in ("get", "list") or any(f.startswith("--get") or f in ("--list", "-l") for f in flags)
        k = keys[0]
        if reading or k + 1 >= len(pos):
            return
        if key == "quality.protected":
            block("quality.protected ni (pre-push ning himoyalangan branchlari) o'zgartirib bo'lmaydi, uni main_guard.py --setup o'rnatadi")
        if not ALLOWED_HOOKS_PATH.match(pos[k + 1]):
            block("core.hooksPath ni faqat .githooks (submodule da ../../.githooks) ga o'rnatish mumkin")

    # --- gh ----------------------------------------------------------------------------------------------------
    def gh(self, args):
        if args[:1] == ["pr"] and "merge" in args and "--admin" in args:
            block("`gh pr merge --admin` himoyani chetlab o'tadi")
        if args[:1] == ["api"]:
            self.gh_api(args[1:])

    def gh_api(self, args):
        method, body, ep, i = None, False, None, 0
        while i < len(args):
            a = args[i]
            i += 1
            if a.startswith("--method="):
                method = a.split("=", 1)[1]
            elif a in ("-X", "--method"):
                method = args[i] if i < len(args) else ""
                i += 1
            elif a.startswith("-X") and len(a) > 2:
                method = a[2:]
            elif a in ("-f", "-F", "--field", "--raw-field", "--input"):
                body = True
                i += 1
            elif a.startswith(("--field=", "--raw-field=", "--input=", "-f", "-F")):
                body = True
            elif a in GH_API_VALUE_OPTS:
                i += 1
            elif not a.startswith("-") and ep is None:
                ep = a.lstrip("/")
        if ep == "graphql":
            if GRAPHQL_DANGER.search(" ".join(args)):
                block("`gh api graphql` orqali merge/ref/commit mutatsiyasi taqiqlangan")
        elif ep and GH_API_PATH.search(ep) and (method or ("POST" if body else "GET")).upper() not in ("GET", "HEAD"):
            block(f"`gh api {ep}` himoyalangan branchni PR siz o'zgartirishi mumkin (faqat GET ruxsat)")


def main():
    try:
        data = json.load(sys.stdin)
        cmd = (data.get("tool_input") or {}).get("command") or ""
    except Exception:
        return 0
    if not cmd:
        return 0
    cwd = data.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    try:
        Guard(cwd).run(cmd)
    except Blocked as e:
        print(f"Git buyrug'i bloklandi: {e}. Qoida: loyiha CLAUDE.md dagi merge qoidasi (himoyalangan branchga faqat PR va gh pr merge orqali).", file=sys.stderr)
        return 2
    except Exception:
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
