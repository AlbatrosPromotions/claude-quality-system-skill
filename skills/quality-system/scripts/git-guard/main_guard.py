#!/usr/bin/env python3
"""Himoyalangan branch (main) himoyasini tekshiradi (GitHub Free da server himoyasi yo'q).

  a) --ref (standart origin/main) ning first-parent tarixida --baseline dan keyingi har commit GitHub serverida yaratilgan
     (committer noreply@github.com; lokal commit da committer foydalanuvchi) va merge_style ga mos bo'lishi shart:
     merge: 2 ota-onali, mavzu "Merge pull request #N from ..."; squash: 1 ota-onali, mavzu "... (#N)" bilan tugaydi; any: ikkalasidan biri
     (ya'ni branchga faqat PR orqali kirgan);
  b) lokal git hook lar yoqilgan: core.hooksPath=.githooks, .githooks/pre-push bajariladi, quality.protected config.json
     bilan mos; .gitmodules dagi har ishga tushirilgan submodule da core.hooksPath = submodule ildizidan <repo>/.githooks gacha nisbiy yo'l.

  python3 .claude/quality/main_guard.py [--repo PATH] [--config PATH] [--ref origin/main] [--baseline SHA]
        [--merge-style merge|squash|any] [--no-hooks-check] [--fetch] [--warn-only] [--setup] [--verbose]

Sozlama .claude/quality/config.json: {"ref": "origin/main", "baseline": "<SHA>", "protected": ["main", "master"], "merge_style": "merge"};
optsiya config dan ustun.
baseline majburiy (undan eski commit lar tekshirilmaydi). --repo standarti: joriy papkaning git ildizi.
Jim chiqadi va 0 qaytaradi; muammo bo'lsa o'zbekcha hisobot va 1. --warn-only: hisobot stdout ga, kod 0 (SessionStart hook uchun).
--setup: bu klonda hook larni yoqadi (core.hooksPath, quality.protected, submodule lar) va tekshiradi; har klonda bir marta.
Faqat standart kutubxona.
"""
import argparse
import json
import os
import re
import subprocess
import sys

MERGE_SUBJECT = re.compile(r"^Merge pull request #\d+ from ")
SQUASH_SUBJECT = re.compile(r"\(#\d+\)$")
GITHUB_COMMITTER = "noreply@github.com"  # merge va squash commit larni GitHub serveri shu committer bilan yaratadi
MERGE_STYLES = ("merge", "squash", "any")
HOOKS_PATH = ".githooks"
DEFAULT_REF = "origin/main"
DEFAULT_PROTECTED = ["main", "master"]
CONFIG_REL = os.path.join(".claude", "quality", "config.json")


def git(repo, *args, timeout=30):
    r = subprocess.run(["git", "-C", repo, *args], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout.strip()


def toplevel(cwd):
    code, out = git(cwd, "rev-parse", "--show-toplevel")
    return out if code == 0 and out else cwd


def load_config(path):
    """(config dict, muammo). Fayl yo'q = bo'sh config, buzuq fayl = muammo."""
    if not os.path.isfile(path):
        return {}, None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        return {}, f"{path} o'qilmadi: {e}"
    if not isinstance(data, dict):
        return {}, f"{path}: JSON obyekt kutilgan"
    return data, None


def protected_list(cfg):
    value = cfg.get("protected")
    if isinstance(value, list) and value and all(isinstance(b, str) and b for b in value):
        return value
    return list(DEFAULT_PROTECTED)


def split_ref(ref):
    """origin/main -> (origin, main); remote siz nom (main) -> (origin, main)."""
    remote, sep, branch = ref.partition("/")
    return (remote, branch) if sep else ("origin", ref)


def is_pr_commit(style, parents, committer, subject):
    """first-parent commit PR orqali kirganmi: GitHub committer i + (merge: 2 ota-ona va "Merge pull request"; squash: 1 ota-ona va "(#N)")."""
    if committer != GITHUB_COMMITTER:
        return False
    as_merge = len(parents) == 2 and bool(MERGE_SUBJECT.match(subject))
    as_squash = len(parents) == 1 and bool(SQUASH_SUBJECT.search(subject))
    return {"merge": as_merge, "squash": as_squash, "any": as_merge or as_squash}[style]


def check_history(repo, ref, baseline, style="merge"):
    """Muammolar ro'yxatini qaytaradi (bo'sh = hammasi joyida)."""
    if style not in MERGE_STYLES:
        return [f"merge_style={style!r} noto'g'ri: {', '.join(MERGE_STYLES)} dan biri kerak"]
    if not baseline:
        return ["baseline ko'rsatilmagan: .claude/quality/config.json da `baseline` (SHA) yoki --baseline kerak; "
                f"hozirdan boshlash uchun: `git rev-parse {ref}` natijasini yozing"]
    remote, branch = split_ref(ref)
    if git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")[0] != 0:
        return [f"{ref} topilmadi: avval `git fetch {remote} {branch}` (yoki --fetch) qiling"]
    if git(repo, "cat-file", "-e", f"{baseline}^{{commit}}")[0] != 0:
        return [f"baseline commit {baseline[:7]} lokal repoda yo'q (shallow clone bo'lishi mumkin): `git fetch --unshallow`"]
    if git(repo, "merge-base", "--is-ancestor", baseline, ref)[0] != 0:
        return [f"baseline {baseline[:7]} {ref} tarixida emas, tarix tekshirilmadi (shallow clone yoki tarix qayta yozilgan)"]
    code, out = git(repo, "log", "--first-parent", "--format=%H%x1f%P%x1f%ce%x1f%s", f"{baseline}..{ref}")
    if code != 0:
        return [f"git log {baseline[:7]}..{ref} xato bilan tugadi"]
    bad = []
    for line in out.splitlines():
        sha, parents, committer, subject = line.split("\x1f", 3)
        if not is_pr_commit(style, parents.split(), committer, subject):
            bad.append(f"    {sha[:7]} {subject}" + ("" if committer == GITHUB_COMMITTER else f"  [committer {committer}]"))
    if not bad:
        return []
    return [f"{ref} tarixida PR orqali kirmagan commit lar ({len(bad)}; merge_style={style}), himoyalangan branchga PR siz kirgan:"] + bad


def submodule_paths(base):
    """base/.gitmodules dagi ishga tushirilgan (init qilingan) submodule lar, ichma-ich ham (mutlaq yo'llar)."""
    if not os.path.isfile(os.path.join(base, ".gitmodules")):
        return []
    code, out = git(base, "config", "-f", ".gitmodules", "-z", "--get-regexp", r"^submodule\..*\.path$")
    if code != 0:
        return []
    found = []
    for item in out.split("\0"):
        _, _, value = item.partition("\n")
        if not value:
            continue
        sub = os.path.normpath(os.path.join(base, value))
        if os.path.exists(os.path.join(sub, ".git")):
            found.append(sub)
            found.extend(submodule_paths(sub))
    return found


def submodule_hooks_path(repo, sub):
    """Submodule ildizidan <repo>/.githooks gacha yo'l (git hook larni submodule ildizida ishga tushiradi): docs/x da ../../.githooks."""
    return os.path.relpath(os.path.join(repo, HOOKS_PATH), sub).replace(os.sep, "/")


def prepush_protected(repo):
    """pre-push hook ning haqiqiy ro'yxati: git config quality.protected (vergul bilan ham bo'ladi), yo'q bo'lsa standart."""
    code, out = git(repo, "config", "--get-all", "quality.protected")
    names = [n for n in re.split(r"[,\s]+", out) if n] if code == 0 else []
    return names or list(DEFAULT_PROTECTED)


def check_hooks(repo, protected):
    problems = []
    code, value = git(repo, "config", "core.hooksPath")
    if code != 0 or value != HOOKS_PATH:
        problems.append(f"core.hooksPath={value or 'yoq'}, kutilgan {HOOKS_PATH}: `git config core.hooksPath {HOOKS_PATH}` (yoki --setup)")
    hook = os.path.join(repo, HOOKS_PATH, "pre-push")
    if not os.path.isfile(hook) or not os.access(hook, os.X_OK):
        problems.append(f"{HOOKS_PATH}/pre-push yo'q yoki bajarilmaydi: `chmod +x {HOOKS_PATH}/pre-push`")
    actual = prepush_protected(repo)
    if sorted(actual) != sorted(protected):
        problems.append(f"pre-push {','.join(actual)} ni himoyalaydi, config.json da esa {','.join(protected)}: --setup")
    for sub in submodule_paths(repo):
        expected = submodule_hooks_path(repo, sub)
        rel = os.path.relpath(sub, repo).replace(os.sep, "/")
        code, value = git(sub, "config", "core.hooksPath")
        if code != 0 or value != expected:
            problems.append(f"{rel} da core.hooksPath={value or 'yoq'}, kutilgan {expected}: "
                            f"`git -C {rel} config core.hooksPath {expected}` (yoki --setup)")
    return problems


def setup_hooks(repo, protected, verbose):
    """Bu klonda hook larni yoqadi. Natijani check_hooks tekshiradi."""
    hook = os.path.join(repo, HOOKS_PATH, "pre-push")
    if os.path.isfile(hook) and not os.access(hook, os.X_OK):
        os.chmod(hook, os.stat(hook).st_mode | 0o111)
    steps = [(repo, ["config", "core.hooksPath", HOOKS_PATH])]
    if protected == DEFAULT_PROTECTED:
        steps.append((repo, ["config", "--unset-all", "quality.protected"]))  # standart: kalit kerak emas (yo'q bo'lsa kod 5, e'tiborsiz)
    else:
        steps.append((repo, ["config", "--replace-all", "quality.protected", ",".join(protected)]))
    steps += [(sub, ["config", "core.hooksPath", submodule_hooks_path(repo, sub)]) for sub in submodule_paths(repo)]
    for where, args in steps:
        code, _ = git(where, *args)
        if code not in (0, 5):
            print(f"ogohlantirish: `git -C {where} {' '.join(args)}` bajarilmadi", file=sys.stderr)
        elif verbose:
            print(f"  git -C {os.path.relpath(where, repo)} {' '.join(args)}")


def main():
    ap = argparse.ArgumentParser(description="himoyalangan branch himoyasini tekshiradi")
    ap.add_argument("--repo", help="standart: joriy papkaning git ildizi")
    ap.add_argument("--config", help="standart: <repo>/.claude/quality/config.json")
    ap.add_argument("--ref", help="standart: config.json ref, yo'q bo'lsa origin/main")
    ap.add_argument("--baseline", help="standart: config.json baseline (majburiy)")
    ap.add_argument("--merge-style", choices=MERGE_STYLES, help="standart: config.json merge_style, yo'q bo'lsa merge")
    ap.add_argument("--no-hooks-check", action="store_true", help="faqat tarixni tekshiradi (CI, hook siz muhit)")
    ap.add_argument("--fetch", action="store_true", help="avval git fetch -q <remote> <branch> (15 s; xato bo'lsa faqat ogohlantiradi)")
    ap.add_argument("--warn-only", action="store_true", help="muammolarni stdout ga yozadi va 0 qaytaradi (SessionStart hook)")
    ap.add_argument("--setup", action="store_true", help="bu klonda hook larni yoqadi (core.hooksPath, quality.protected, submodule lar)")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()

    repo = os.path.abspath(a.repo) if a.repo else toplevel(os.getcwd())
    cfg, cfg_problem = load_config(a.config or os.path.join(repo, CONFIG_REL))
    ref = a.ref or cfg.get("ref") or DEFAULT_REF
    baseline = a.baseline or cfg.get("baseline")
    protected = protected_list(cfg)
    style = a.merge_style or cfg.get("merge_style") or "merge"

    if git(repo, "rev-parse", "--git-dir")[0] != 0:
        print(f"{repo} git repo emas (--repo ni tekshiring)", file=sys.stderr)
        return 0 if a.warn_only else 1

    if a.setup:
        setup_hooks(repo, protected, a.verbose)

    if a.fetch:
        remote, branch = split_ref(ref)
        try:
            code, _ = git(repo, "fetch", "-q", remote, branch, timeout=15)
        except subprocess.TimeoutExpired:
            code = -1
        if code != 0:
            print(f"ogohlantirish: git fetch {remote} {branch} bajarilmadi, lokal {ref} eskirgan bo'lishi mumkin", file=sys.stderr)

    problems = [cfg_problem] if cfg_problem else []
    problems += check_history(repo, ref, baseline, style)
    history_ok = not problems
    if not a.no_hooks_check:
        problems += check_hooks(repo, protected)

    if not problems:
        if a.verbose:
            print(f"git himoyasi: tarix toza ({ref}, baseline {baseline[:7]}dan keyin faqat PR, merge_style={style})"
                  + ("" if a.no_hooks_check else ", hook lar yoqilgan"))
        return 0
    if a.verbose and history_ok:
        print(f"git himoyasi: tarix toza ({ref})")
    out = sys.stdout if a.warn_only else sys.stderr
    print("git himoyasi: muammolar topildi", file=out)
    for p in problems:
        print(p if p.startswith("    ") else f"  - {p}", file=out)
    return 0 if a.warn_only else 1


if __name__ == "__main__":
    sys.exit(main())
