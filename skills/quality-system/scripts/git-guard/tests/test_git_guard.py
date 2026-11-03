#!/usr/bin/env python3
"""git himoyasi testlari: guard-git.py (PreToolUse), pre-push va main_guard.py.

Qora quti: skriptlar subprocess orqali ishga tushiriladi, hamma git repo vaqtinchalik papkada
(haqiqiy repo konfigiga tegilmaydi). Ikki joylashuvni biladi: to'plam papkasi (guard-git.py, pre-push, main_guard.py yonma-yon)
va o'rnatilgan loyiha (.claude/hooks, .githooks, .claude/quality).

  python3 -m unittest discover -s .claude/quality/tests -v
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

QUALITY = Path(__file__).resolve().parent.parent  # to'plam papkasi yoki <loyiha>/.claude/quality
if (QUALITY / "guard-git.py").is_file():
    HOOK, PRE_PUSH, MAIN_GUARD = QUALITY / "guard-git.py", QUALITY / "pre-push", QUALITY / "main_guard.py"
else:
    PROJECT = QUALITY.parent.parent
    HOOK, PRE_PUSH, MAIN_GUARD = PROJECT / ".claude" / "hooks" / "guard-git.py", PROJECT / ".githooks" / "pre-push", QUALITY / "main_guard.py"

HOME = tempfile.mkdtemp(prefix="guard-home-")
ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_") and k != "CLAUDE_PROJECT_DIR"}
ENV.update(
    HOME=HOME, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1",
    GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com",
)


def tearDownModule():
    shutil.rmtree(HOME, ignore_errors=True)


GITHUB = {"GIT_COMMITTER_EMAIL": "noreply@github.com", "GIT_COMMITTER_NAME": "GitHub"}  # GitHub serveri yaratgan commit committer i


def git(cwd, *args, check=True, input=None, env_extra=None):
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, env=dict(ENV, **(env_extra or {})), input=input)
    if check and r.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} ({cwd}): {r.stderr}")
    return r


def commit(repo, name, message=None, env_extra=None):
    (Path(repo) / name).write_text(name)
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", message or name, env_extra=env_extra)
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def run_hook(command, cwd, env_extra=None, raw_stdin=None):
    env = dict(ENV, **(env_extra or {}))
    stdin = raw_stdin if raw_stdin is not None else json.dumps({"tool_input": {"command": command}, "cwd": str(cwd) if cwd else None})
    return subprocess.run([sys.executable, str(HOOK)], input=stdin, capture_output=True, text=True, env=env)


PR_BODY = """gh pr create --title t --body "$(cat <<'EOF'
## Xulosa
Hech qachon git push origin main yoki git push --force ishlatmang.
EOF
)\""""

COMMIT_MSG = """git commit -m "$(cat <<'EOF'
fix: git push origin main matni

Co-Authored-By: x
EOF
)\""""

# (repo, buyruq, bloklanadimi). repo: main = main branchdagi repo, feat = feature branchdagi, plain = repo emas.
# @MAIN@ va @FEAT@ repo yo'llariga almashtiriladi.
CASES = [
    # --- git push: main/master ga ---
    ("feat", "git push origin main", True),
    ("feat", "git push origin master", True),
    ("feat", "git push origin HEAD:main", True),
    ("feat", "git push origin HEAD:refs/heads/main", True),
    ("feat", "git push origin feature:refs/heads/main", True),
    ("feat", "git push origin :main", True),
    ("feat", "git push origin --delete main", True),
    ("feat", "git push -u origin main", True),
    ("feat", "git push origin 'refs/heads/*:refs/heads/*'", True),
    ("main", "git push", True),
    ("main", "git push origin", True),
    ("main", "git push origin HEAD", True),
    ("main", "git push origin 2>&1", True),
    ("feat", "git -C @MAIN@ push", True),
    ("feat", "cd @MAIN@ && git push", True),
    ("plain", "cd /tmp && git push origin main", True),
    # --- git push: force va hook ni chetlash ---
    ("feat", "git push --force origin feature", True),
    ("feat", "git push -f origin feature", True),
    ("feat", "git push -uf origin feature", True),
    ("feat", "git push --force-with-lease origin feature", True),
    ("feat", "git push --force-with-lease=feature:abc123 origin feature", True),
    ("feat", "git push --force-if-includes origin feature", True),
    ("feat", "git push origin +feature", True),
    ("feat", "git push origin +feature:feature", True),
    ("feat", "git push --no-verify origin feature", True),
    ("feat", "git push --no-ver origin feature", True),
    ("feat", "git push --mirror origin", True),
    ("feat", "git push --all origin", True),
    ("feat", "git push --branches origin", True),
    ("feat", "git push origin feature --force", True),
    # --- git push: ruxsat ---
    ("feat", "git push origin feature", False),
    ("feat", "git push -u origin feature", False),
    ("feat", "git push --set-upstream origin feature", False),
    ("feat", "git push origin HEAD", False),
    ("feat", "git push origin HEAD:feature", False),
    ("feat", "git push origin feature:feature", False),
    ("feat", "git push origin --delete feature", False),
    ("feat", "git push -o ci.skip origin feature", False),
    ("feat", "git push origin refs/tags/v1", False),
    ("feat", "git push --tags", False),
    ("feat", "git push", False),
    ("feat", "git push origin 2>&1 | tail -3", False),
    ("main", "git push origin feature", False),
    ("main", "git push origin feature:feature", False),
    ("plain", "git push", False),
    # --- main da commit yaratadigan buyruqlar ---
    ("main", "git commit -m x", True),
    ("main", "git commit --amend --no-edit", True),
    ("main", "git cherry-pick abc123", True),
    ("main", "git revert HEAD", True),
    ("main", "git am fix.patch", True),
    ("main", "git merge feature", True),
    ("main", "git merge --no-ff feature", True),
    ("feat", "cd @MAIN@ && git commit -m x", True),
    ("feat", "git -C @MAIN@ commit -m x", True),
    ("feat", "git status; git -C @MAIN@ revert HEAD", True),
    ("feat", "git switch main && git commit -m x", True),
    ("feat", "git checkout main && git commit -m x", True),
    # --- feature branchda yoki xavfsiz ---
    ("feat", "git commit -m x", False),
    ("feat", "git merge main", False),
    ("feat", "git cherry-pick abc123", False),
    ("main", "git pull --ff-only", False),
    ("main", "git pull", False),
    ("main", "git merge --ff-only feature", False),
    ("main", "git merge --abort", False),
    ("main", "git fetch origin && git status && git log --oneline -3", False),
    ("main", "git switch -c fix/x && git commit -m x", False),
    ("main", "git checkout -b fix/x && git commit -m x", False),
    ("main", "git checkout feature && git commit -m x", False),
    ("main", "git -C @FEAT@ commit -m x", False),
    ("main", "cd @FEAT@ && git commit -m x", False),
    ("feat", "(cd @MAIN@ && git status); git commit -m x", False),
    ("plain", "git commit -m x", False),
    # --- o'rash va ichma-ich buyruqlar ---
    ("feat", 'bash -c "git push origin main"', True),
    ("feat", "sh -c 'git push origin main'", True),
    ("feat", "bash -lc 'cd @MAIN@ && git commit -m x'", True),
    ("feat", "echo $(git push origin main)", True),
    ("feat", 'echo "$(git push origin main)"', True),
    ("feat", "echo `git push origin main`", True),
    ("feat", 'eval "git push origin main"', True),
    ("feat", "FOO=1 git push origin main", True),
    ("feat", "env GIT_TRACE=1 git push origin main", True),
    ("feat", "sudo git push origin main", True),
    ("feat", "command git push origin main", True),
    ("feat", "/usr/bin/git push origin main", True),
    ("feat", "timeout 20 git push origin main", True),
    ("feat", "git status && git push origin main", True),
    ("feat", "git push origin feature; git push origin main", True),
    ("feat", "git status\ngit push origin main", True),
    ("feat", "git push \\\n  origin main", True),
    ("feat", "cat <<EOF\nx\nEOF\ngit push origin main", True),
    ("feat", 'git push --force origin "feature', True),
    # --- argument matni buyruq emas ---
    ("feat", 'echo "git push --force"', False),
    ("feat", "echo 'git push origin main'", False),
    ("feat", 'echo "git push origin main" | cat', False),
    ("feat", "git push origin feature # main emas", False),
    ("feat", "grep -rn 'git push' docs/", False),
    ("feat", "gh pr create --body 'Kod: `git push origin main` taqiqlangan'", False),
    ("feat", PR_BODY, False),
    ("feat", COMMIT_MSG, False),
    ("feat", "cat <<'EOF'\ngit push origin main\nEOF", False),
    ("feat", "cat <<EOF > notes.txt\ngit push --force\nEOF\ngit status", False),
    ("feat", "echo \"it's", False),
    ("feat", "make ci", False),
    # --- git config core.hooksPath ---
    ("feat", "git config core.hooksPath /dev/null", True),
    ("feat", "git config --global core.hooksPath ''", True),
    ("feat", "git config --unset core.hooksPath", True),
    ("feat", "git config --unset-all core.hookspath", True),
    ("feat", "git config set core.hooksPath /tmp/x", True),
    ("feat", "git config unset core.hooksPath", True),
    ("feat", "git -c core.hooksPath=/dev/null push origin feature", True),
    ("feat", "git -c CORE.HOOKSPATH=x commit -m x", True),
    ("feat", "GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=/dev/null GIT_CONFIG_COUNT=1 git push origin feature", True),
    ("feat", "git config core.hooksPath", False),
    ("feat", "git config --get core.hooksPath", False),
    ("feat", "git config --local --get core.hooksPath", False),
    ("feat", "git config get core.hooksPath", False),
    ("feat", "git config --list", False),
    ("feat", "git config core.hooksPath .githooks", False),
    ("feat", "git config core.hooksPath ../../.githooks", False),
    ("feat", "git config core.hooksPath ../../../.githooks", False),
    ("feat", "git config core.hooksPath ../.githooks", False),
    ("feat", "git config core.hooksPath ../x", True),
    ("feat", "git config core.hooksPath .githooks2", True),
    ("feat", "git config core.hooksPath ../../hooks", True),
    ("feat", "git config core.hooksPath /tmp/.githooks", True),
    # --- quality.protected (pre-push ning himoyalangan branchlari) ---
    ("feat", "git config quality.protected develop", True),
    ("feat", "git config --add quality.protected develop", True),
    ("feat", "git config --unset-all quality.protected", True),
    ("feat", "git -c quality.protected=x push origin feature", True),
    ("feat", "GIT_CONFIG_KEY_0=quality.protected GIT_CONFIG_VALUE_0=x GIT_CONFIG_COUNT=1 git push origin feature", True),
    ("feat", "git config --get-all quality.protected", False),
    ("feat", "git config quality.protected", False),
    ("feat", "git config user.name x", False),
    ("feat", "git -c user.name=x commit -m x", False),
    # --- gh ---
    ("feat", "gh pr merge 5 --merge", False),
    ("feat", "gh pr merge 5 --merge --delete-branch", False),
    ("feat", "gh pr view 5 --json state", False),
    ("feat", "gh pr merge 5 --merge --admin", True),
    ("feat", "gh pr merge --admin 5", True),
    ("feat", "gh api repos/o/r/git/refs/heads/main -X PATCH -f sha=abc123", True),
    ("feat", "gh api repos/o/r/git/refs -f ref=refs/heads/x -f sha=abc123", True),
    ("feat", "gh api --method DELETE repos/o/r/git/refs/heads/x", True),
    ("feat", "gh api repos/o/r/merges -f base=main -f head=x", True),
    ("feat", "gh api repos/o/r/pulls/5/merge -X PUT", True),
    ("feat", "gh api -XPUT repos/o/r/contents/x.txt -f message=m", True),
    ("feat", "gh api repos/o/r/contents/x.txt --input body.json", True),
    ("feat", "gh api graphql -f query='mutation { mergePullRequest(input: {pullRequestId: \"x\"}) { clientMutationId } }'", True),
    ("feat", "gh api repos/o/r/git/refs/heads/main", False),
    ("feat", "gh api repos/o/r/contents/x.txt", False),
    ("feat", "gh api repos/o/r/contents/x.txt -X GET -f ref=main", False),
    ("feat", "gh api repos/o/r/pulls/5", False),
    ("feat", "gh api repos/o/r/pulls -f title=t -f head=x -f base=main", False),
    ("feat", "gh api graphql -f query='{ viewer { login } }'", False),
    # --- make ci izi (sos-ci-ok, sos-review-ok) ---
    ("feat", "echo ok > .git/sos-ci-ok", True),
    ("feat", "echo ok >> .git/sos-review-ok", True),
    ("feat", "echo ok >| .git/sos-ci-ok", True),
    ("feat", "echo ok 1>.git/sos-ci-ok", True),
    ("feat", "echo ok &> .git/sos-ci-ok", True),
    ("feat", "cat /tmp/x > $(git rev-parse --git-path sos-ci-ok)", True),
    ("feat", "cp /tmp/x .git/sos-ci-ok", True),
    ("feat", "mv /tmp/x .git/sos-review-ok", True),
    ("feat", "touch .git/sos-ci-ok", True),
    ("feat", "rm -f .git/sos-ci-ok", True),
    ("feat", "echo x | tee .git/sos-ci-ok", True),
    ("feat", "printf x | dd of=.git/sos-ci-ok", True),
    ("feat", "python3 -c \"open('.git/sos-ci-ok','w').write('x')\"", True),
    ("feat", "sed -i 's/a/b/' .git/sos-ci-ok", True),
    ("feat", "sed -ni 'p' .git/sos-ci-ok", True),
    ("feat", "sed --in-place s/a/b/ .git/sos-ci-ok", True),
    ("feat", "bash -c 'echo ok > .git/sos-ci-ok'", True),
    ("feat", "cat .git/sos-ci-ok", False),
    ("feat", "head -c 20 .git/sos-ci-ok", False),
    ("feat", "grep -c x .git/sos-review-ok", False),
    ("feat", "ls -l .git/sos-ci-ok", False),
    ("feat", "stat .git/sos-ci-ok", False),
    ("feat", "wc -c .git/sos-ci-ok", False),
    ("feat", "diff .git/sos-ci-ok .git/other", False),
    ("feat", "cut -d' ' -f1 .git/sos-ci-ok", False),
    ("feat", "test -f .git/sos-ci-ok", False),
    ("feat", "[ -f .git/sos-ci-ok ] && echo bor", False),
    ("feat", "sed -n 1p .git/sos-ci-ok", False),
    ("feat", "git rev-parse --git-path sos-ci-ok", False),
    ("feat", 'echo "$(cat .git/sos-ci-ok)" > /tmp/out.txt', False),
    ("feat", "echo ok > /tmp/out.txt 2>&1", False),
    # --- umumlashtirilgan izlar: <prefiks>-ci-ok, <prefiks>-review-ok, prefikssiz ci-ok va review-ok ---
    ("feat", "echo ok > .git/ci-ok", True),
    ("feat", "echo ok > .git/review-ok", True),
    ("feat", "echo ok >> .git/myproj-ci-ok", True),
    ("feat", "echo ok > .git/my_proj2-review-ok", True),
    ("feat", "cat /tmp/x > $(git rev-parse --git-path ci-ok)", True),
    ("feat", "cat /tmp/x > $(git rev-parse --git-path myproj-ci-ok)", True),
    ("feat", "echo ok > `git rev-parse --git-path myproj-review-ok`", True),
    ("feat", "cp /tmp/x .git/myproj-ci-ok", True),
    ("feat", "touch .git/ci-ok", True),
    ("feat", "rm -f .git/myproj-review-ok", True),
    ("feat", "echo x | tee .git/myproj-ci-ok", True),
    ("feat", "sed -i 's/a/b/' .git/ci-ok", True),
    ("feat", "bash -c 'echo ok > .git/myproj-ci-ok'", True),
    ("feat", "echo ok > .git/myproj-ci-ok \"x", True),
    ("feat", "cat .git/ci-ok", False),
    ("feat", "cat .git/myproj-ci-ok", False),
    ("feat", "grep -c x .git/myproj-review-ok", False),
    ("feat", "test -f .git/ci-ok", False),
    ("feat", "echo ok > .git/ci-okay", False),
    ("feat", "echo ok > notes-ci.txt", False),
    ("feat", "echo ok > /tmp/ci-ok2", False),
    ("feat", "git rev-parse --git-path myproj-ci-ok", False),
    # --- shell kalit so'zlari va guruhlash: buyruq `then`, `do`, `{`, `!` dan keyin ham buyruq ---
    ("feat", "if true; then git push origin main; fi", True),
    ("feat", "if true; then git push --no-verify origin feature; fi", True),
    ("feat", "if false; then :; else git push --force origin feature; fi", True),
    ("feat", "if false; then :; elif true; then git push origin main; fi", True),
    ("feat", "if git push origin main; then echo ok; fi", True),
    ("feat", "for i in 1 2; do git push --no-verify origin feature; done", True),
    ("feat", "while true; do git push origin main; done", True),
    ("feat", "until false; do git push origin main; done", True),
    ("feat", "{ git push origin main; }", True),
    ("feat", "! git push --no-verify origin feature", True),
    ("feat", "if true\nthen\n  git push origin main\nfi", True),
    ("feat", "for i in 1\ndo\n  git push origin main\ndone", True),
    ("feat", "f() { git push --no-verify origin feature; }; f", True),
    ("feat", "function f { git push origin main; }; f", True),
    ("feat", "function f() { git push origin main; }; f", True),
    ("feat", "coproc git push origin main", True),
    ("feat", "case x in x) git push origin main;; esac", True),
    ("feat", "if true; then git -c core.hooksPath=/dev/null status; fi", True),
    ("feat", "if true; then gh pr merge 1 --admin; fi", True),
    ("feat", "if true; then gh api -X PUT repos/o/r/pulls/1/merge; fi", True),
    ("feat", "if true; then cd @MAIN@ && git commit -m x; fi", True),
    ("main", "if true; then git commit -m x; fi", True),
    ("main", "for i in 1; do git merge feature; done", True),
    ("main", "git switch feature && if true; then git commit -m x; fi", False),
    ("feat", "if git diff --quiet; then echo clean; fi", False),
    ("feat", 'for f in a b; do git add "$f"; done', False),
    ("feat", "if git push origin feature; then echo ok; fi", False),
    ("feat", 'for t in git push --force; do echo "$t"; done', False),
    ("main", 'for t in git push; do echo "$t"; done', False),
    ("feat", "case git in git) echo push --force;; esac", False),
    ("feat", 'select t in git push --force; do echo "$t"; done', False),
    ("feat", "if true; then echo git push origin main; fi", False),
    ("feat", "if echo git push --force; then :; fi", False),
    ("feat", "if false; then :; elif echo git push --force; then :; fi", False),
    ("feat", "if false; then :; else echo git push --force; fi", False),
    ("feat", "while echo git push --force; do :; done", False),
    ("feat", "until echo git push --force; do :; done", False),
    ("feat", "for i in 1; do echo git push --force; done", False),
    ("feat", "! echo git push --force", False),
    ("feat", "{ echo git push --force; }", False),
    ("feat", "coproc echo git push --force", False),
    ("feat", "function f { echo git push --force; }; f", False),
    ("feat", "if true; then cat .git/ci-ok; fi", False),
    # --- noma'lum o'ramlar: argumentlari ichida git, gh yoki shell boshlanadi ---
    ("feat", "caffeinate git push --no-verify origin feature", True),
    ("feat", "caffeinate -i git push origin main", True),
    ("feat", "xargs git push --no-verify origin feature", True),
    ("feat", "echo x | xargs -n1 git push origin main", True),
    ("main", "echo x | xargs git push origin", True),
    ("feat", "find . -maxdepth 0 -exec git push --no-verify origin feature \\;", True),
    ("feat", "find . -maxdepth 0 -execdir git push origin main {} +", True),
    ("feat", "find . -exec git status \\; -exec git push origin main \\;", True),
    ("feat", "parallel git status ::: git push origin main", True),
    ("feat", "flock /tmp/l git push origin main", True),
    ("feat", "stdbuf -oL git push origin main", True),
    ("feat", "caffeinate bash -c 'git push origin main'", True),
    ("feat", "xargs sh -c 'git push origin main'", True),
    ("feat", "find . -exec sh -c 'git push --force origin feature' \\;", True),
    ("feat", "caffeinate gh pr merge 1 --admin", True),
    ("feat", "caffeinate eval 'git push origin main'", True),
    ("feat", "git branch | xargs -n1 git log -1", False),
    ("feat", "find . -name '*.md' -exec git add {} \\;", False),
    ("feat", "which git", False),
    ("feat", "command -v git", False),
    ("feat", "brew install git gh", False),
    ("feat", "grep -rn git push --force docs", False),
    ("main", "grep -rn git push docs", False),
    ("feat", "sed -n s/git/x/p notes.md", False),
    # --- shell ga stdin orqali berilgan matn ---
    ("feat", "bash <<< 'git push --no-verify origin feature'", True),
    ("feat", "caffeinate bash <<< 'git push origin main'", True),
    ("feat", "bash <<'EOF'\ngit push --no-verify origin feature\nEOF", True),
    ("feat", "sh -s <<EOF\ngit push origin main\nEOF", True),
    ("feat", "cat <<'EOF' | bash\ngit push origin main\nEOF", True),
    ("feat", "/bin/zsh <<-EOF\n\tgit push origin main\n\tEOF", True),
    ("feat", "echo 'git push --no-verify origin feature' | bash", True),
    ("feat", "printf '%s\\n' 'git push origin main' | sudo sh", True),
    ("feat", "echo 'git push origin main' | caffeinate bash", True),
    ("feat", "echo 'git push origin main' |& bash", True),
    ("feat", "cat <<'EOF' | bash | tee out.log\ngit push origin main\nEOF", True),
    ("feat", "echo 'git push origin main'; bash -n x.sh", False),
    ("feat", "bash <<< 'git status'", False),
    ("feat", "bash <<'EOF'\ngit status\nEOF", False),
    ("feat", "echo 'git status' | bash", False),
    ("feat", "echo 'git push origin main' | tee notes.md", False),
    ("feat", "echo 'git push origin main' > notes.md; cat notes.md | wc -l", False),
    ("feat", "cat <<'EOF' > notes.md\ngit push origin main\nEOF", False),
    ("feat", "cat <<'EOF' | tee notes.md\ngit push --force origin main\nEOF", False),
    ("feat", "python3 - <<'EOF'\nprint('git push origin main')\nEOF", False),
    # --- shell optsiyalari: qiymat oladiganlari, -s, -n, -c, skript fayli ---
    ("feat", "bash -euo pipefail <<'EOF'\ngit push --no-verify origin feature\nEOF", True),
    ("feat", "cat <<'EOF' | bash -euo pipefail\ngit push origin main\nEOF", True),
    ("feat", "bash -O extglob <<'EOF'\ngit push origin main\nEOF", True),
    ("feat", "bash -s -- arg <<EOF\ngit push origin main\nEOF", True),
    ("feat", "bash - <<'EOF'\ngit push origin main\nEOF", True),
    ("feat", "bash /dev/stdin <<EOF\ngit push origin main\nEOF", True),
    ("feat", "bash \\\n <<EOF\ngit push origin main\nEOF", True),
    ("feat", "bash -c -- 'git push --no-verify origin main'", True),
    ("feat", "bash -euo pipefail -c 'git push origin main'", True),
    ("feat", "source /dev/stdin <<< 'git push origin main'", True),
    ("feat", ". /dev/stdin <<< 'git push origin main'", True),
    ("feat", "bash x.sh <<< 'git push origin main'", False),
    ("feat", "echo 'git push origin main' | bash x.sh", False),
    ("feat", "echo 'git push origin main' | bash -c 'cat > notes.md'", False),
    ("feat", "echo 'git push origin main' | bash -n", False),
    ("feat", "source x.sh <<< 'git push origin main'", False),
    # --- heredoc tanasi o'z buyrug'iga tegishli, tartib va cwd saqlanadi ---
    ("feat", "git switch main && bash <<'EOF'\ngit commit -m x\nEOF", True),
    ("feat", "cd @MAIN@ && bash <<'EOF'\ngit commit -m x\nEOF", True),
    ("main", "git switch feature && bash <<'EOF'\ngit commit -m x\nEOF", False),
    ("feat", "cat <<EOF > a\ngit status\nEOF\nbash <<EOF\ngit push origin main\nEOF", True),
    ("feat", "cat <<EOF > a\ngit push origin main\nEOF\nbash <<EOF\ngit status\nEOF", False),
    ("feat", "echo \"$(bash <<'EOF'\ngit push origin main\nEOF\n)\"", True),
    ("feat", "gh pr create --title 'guard: cover bash <<EOF' --body-file - <<'EOF'\nDo not run git push origin main\nEOF", False),
    ("feat", "cat <<'EOF' | grep -c bash\ngit push origin main\nEOF", False),
    ("feat", "cat <<'EOF' > tools/bash\ngit push origin main\nEOF", False),
    ("feat", "cat <<'EOF' > notes.md # later feed to bash\ngit push origin main\nEOF", False),
    # --- tirnoqsiz heredoc ichidagi $(...) bajariladi ---
    ("feat", "cat <<EOF\n$(git push --no-verify origin feature)\nEOF", True),
    ("feat", "cat <<EOF\ngit push origin main\nEOF", False),
    ("feat", "cat <<EOF\n\\$(git push origin main)\nEOF", False),
    ("feat", "cat <<'EOF'\n$(git push origin main)\nEOF", False),
    ("feat", "cat <<\\EOF\n$(git push origin main)\nEOF", False),
    # --- quvur shakllari va printf qochishlari ---
    ("feat", "printf 'git status\\ngit push --no-verify origin feature\\n' | bash", True),
    ("feat", "printf 'git push origin main\\n' | bash", True),
    ("feat", "echo 'git push origin main' |\nbash", True),
    ("feat", "echo 'git push origin main' | (bash)", True),
    ("feat", "(echo 'git push origin main') | bash", True),
    ("feat", "{ echo 'git push origin main'; } | bash", True),
    ("feat", "echo 'git push origin main' | tee /dev/null | bash", True),
    ("feat", "(cat <<'EOF'\ngit push origin main\nEOF\n) | bash", True),
    ("feat", "echo 'git push origin main'; ls | bash", False),
    ("feat", "echo 'git push origin main' | true; bash", False),
    ("feat", "watch 'eval git push origin main'", True),
    ("feat", "echo 'git push origin main'; true\nbash -n x.sh", False),
    ("feat", "echo 'git push origin main' | cat x.txt | bash", False),
    ("feat", "echo $'git push --no-verify origin main' | bash", True),
    ("feat", "bash <<< $'git push origin main'", True),
    ("feat", "git push $'--no-verify' origin feature", True),
    # --- git ning o'zi buyruq bajaradigan joylar ---
    ("feat", "git submodule foreach git push --no-verify origin feature", True),
    ("feat", "git submodule foreach --recursive 'git push origin main'", True),
    ("feat", "git rebase --exec 'git push --no-verify origin feature' HEAD~1", True),
    ("feat", "git rebase -x 'git push origin main' HEAD~1", True),
    ("feat", "git rebase --exec='git push origin main' HEAD~1", True),
    ("feat", "git bisect run git push --no-verify origin feature", True),
    ("feat", "git -c alias.p='push --no-verify' p origin feature", True),
    ("feat", "git -c alias.p='!git status' p", True),
    ("feat", "git config alias.p '!git push origin main'", True),
    ("feat", "git config alias.p 'push --no-verify'", True),
    ("feat", "git submodule foreach git status", False),
    ("feat", "git submodule update --init", False),
    ("feat", "git rebase --exec 'make test' HEAD~1", False),
    ("feat", "git rebase main", False),
    ("feat", "git bisect run make test", False),
    ("feat", "git bisect start", False),
    ("feat", "git -c alias.st=status st", False),
    ("feat", "git config --get alias.p", False),
    ("feat", "git config alias.st status", False),
    # --- for ... do, env -S, tirnoq ichida buyruq oladigan vositalar, -ok ---
    ("feat", "set -- 1; for i do git push --no-verify origin main; done", True),
    ("feat", "env -S 'git push --no-verify origin feature'", True),
    ("feat", "trap 'git push --no-verify origin feature' EXIT", True),
    ("feat", "flock /tmp/l -c 'git push origin main'", True),
    ("feat", "watch 'git push origin main'", True),
    ("feat", "parallel 'git push origin main' ::: 1", True),
    ("feat", "script -c 'git push origin main' /dev/null", True),
    ("feat", "su -c 'git push origin main' me", True),
    ("feat", "find . -ok git push origin main \\;", True),
    ("feat", "find . -okdir git push origin main \\;", True),
    ("feat", "for i do echo git push --force; done", False),
    ("feat", "trap 'rm -f x' EXIT", False),
    ("feat", "watch 'ls -l'", False),
    ("feat", "python3 -c 'print(1)' 'two words'", False),
    ("main", "man git push", False),
    ("feat", ": git push --force", False),
    ("feat", "egrep -rn git push --force docs", False),
    ("feat", "fgrep -rn git push --force docs", False),
    ("main", "find . -name git -o -name push", False),
    # --- $(...) bitta so'z: yonidagi optsiyalar yo'qolmaydi ---
    ("feat", "git push origin $(git branch --show-current) --no-verify", True),
    ("feat", "git push $(git remote) --force origin feature", True),
    ("feat", "git -C $(git rev-parse --show-toplevel) push --no-verify origin feature", True),
    ("feat", "git push origin `git branch --show-current` -f", True),
    ("feat", "git push origin `git branch; true` --no-verify", True),
    ("feat", "bash -c $'git\\tpush\\t--no-verify\\torigin\\tfeature'", True),
    ("feat", 'touch "$(git rev-parse --git-path x-ci-ok)"', True),
    ("main", "cd $(git rev-parse --show-toplevel) && git commit -m x", True),
    ("main", "echo \"$(echo \"it's\")\"; git commit -m x", True),
    ("feat", "git push origin $(git branch --show-current)", False),
    ("feat", "git -C $(git rev-parse --show-toplevel) status", False),
    # --- ikkinchi ko'rik topilmalari ---
    ("feat", "echo 'git push origin main' | cat - | bash", True),
    ("feat", '" "; git push --no-verify origin feature', True),
    ("feat", "bash --rcfile /dev/null -c 'git push --no-verify origin feature'", True),
    ("feat", "bash --init-file /dev/null -c 'git push origin main'", True),
    ("feat", "bash /dev/fd/0 <<'EOF'\ngit push origin main\nEOF", True),
    ("feat", "fish --command 'git push origin main'", True),
    ("feat", "bash <<'EOF'\ngit push --no-verify origin feature\nEOF\nX=$(cat <<'EOF'\nhello\nEOF\n)", True),
    ("feat", "cat <<'EOF' > a\nhello\nEOF\necho \"$(bash <<'EOF'\ngit push origin main\nEOF\n)\"", True),
    ("feat", "bash -c $'git status\\ngit push --no-verify origin main'", True),
    ("feat", "eval $'git status\\tx\\ngit push origin main'", True),
    ("feat", 'bash -c $"git push origin main"', True),
    ("feat", "echo $'it\\'s'; git -c core.hooksPath=/dev/null push origin feature", True),
    ("feat", "git bisect run sh -c 'git push --no-verify origin feature'", True),
    ("feat", "git submodule foreach sh -c 'git push origin main'", True),
    ("feat", "git send-pack ../bare.git HEAD:refs/heads/main", True),
    ("feat", "git http-push ../x HEAD:refs/heads/main", True),
    ("feat", "cat <<EOF\nIt's $(git push --no-verify origin feature)\nEOF", True),
    ("feat", "xargs /usr/bin/git push origin main", True),
    ("feat", "printf 'git\\tpush\\t--no-verify\\torigin\\tfeature\\n' | bash", True),
    ("feat", "cat <<'EOF' > a\ngit push origin main\nEOF\necho \"$(bash <<'EOF'\ngit status\nEOF\n)\"", False),
    ("feat", "sed -i 's/git push --force //' README.md", False),
    ("feat", "sed -i '/git push origin main /d' README.md", False),
    ("feat", "awk '/git push --force / {print}' a.md", False),
    ("feat", "echo 'git push origin main' | tee tools/bash", False),
    ("feat", "cat <<'EOF' | tee /tmp/sh\ngit push origin main\nEOF", False),
    ("feat", "git config --get alias.p push", False),
    ("feat", "bash --rcfile /dev/null x.sh <<< 'git push origin main'", False),
    # --- uchinchi ko'rik: $ tirnoqqa yondosh, foreach matni, qidiruv matni, iz nomi ---
    ("feat", 'grep -q "a\\$" f && git push --force origin main && grep -q "b\\$" g', True),
    ("feat", 'git ls-files | grep "\\.py\\$" | wc -l; git push --no-verify; git ls-files | grep "\\.md\\$"', True),
    ("main", 'grep "\\.py\\$" f; git commit -m x', True),
    ("feat", 'grep "\\.py\\$" f; git config --unset core.hooksPath', True),
    ("feat", 'grep "a\\$" f; git push -f; true', True),
    ("main", "echo \\$'a' && git commit -m x", True),
    ("feat", "grep 'foo$' f; git push --force; grep 'bar$' g", True),
    ("feat", 'grep "foo$" f; git push --force; grep "bar$" g', True),
    ("feat", 'echo "cost \\$"; echo "git push origin main"', False),
    ("feat", 'echo "$\'x\'"; echo "git push origin main"', False),
    ("feat", 'grep "foo$" f', False),
    ("feat", 'git submodule foreach "git add . && git push --no-verify"', True),
    ("feat", "git submodule foreach 'git status; git push --no-verify'", True),
    ("feat", "git submodule foreach 'git add -A && git commit -m x && git push origin main'", True),
    ("feat", "git submodule --quiet foreach 'git status && git config core.hooksPath /dev/null'", True),
    ("feat", "git submodule foreach --recursive git status", False),
    ("feat", "git bisect run 'make test && git status'", False),
    ("feat", "find . -name '*.md' | xargs grep -l 'git push origin main'", False),
    ("feat", "find . -name '*.md' -exec grep -l 'git push origin main' {} +", False),
    ("feat", "git ls-files | xargs grep -n 'git push --force'", False),
    ("feat", "stdbuf -oL grep 'git push origin main' f", False),
    ("feat", "ag 'git push origin main'", False),
    ("feat", "ack 'git push --force'", False),
    ("feat", "rm $(git rev-parse --git-path x-ci-ok)", True),
    ("feat", "for f in $(ls *-ci-ok); do echo $f; done", False),
    ("feat", "[[ -f $(git rev-parse --git-path x-ci-ok) ]] && echo ok", False),
    ("feat", "basename $(git rev-parse --git-path x-ci-ok)", False),
    ("feat", "dirname $(git rev-parse --git-path x-ci-ok)", False),
    ("feat", "export S=$(git rev-parse --git-path x-ci-ok)", False),
    ("feat", "python3 x.py $(grep -l ci-ok f)", False),
    ("feat", "git config --global alias.sp 'stash push'", False),
    ("feat", 'git config --global alias.lg "log --grep=push"', False),
    ("feat", "flock /tmp/l -c 'cd x && git push -f'", True),
    ("feat", "tmux new-session -d 'cd x; git push -f'", True),
    ("feat", "tool --note 'a; b c'", False),
    ("feat", "git config --remove-section core", True),
    ("feat", "git config --remove-section quality", True),
    ("feat", "git config --rename-section core x", True),
    ("feat", "git config --remove-section alias", False),
    # --- to'rtinchi ko'rik: skaner faqat qidiruv vositasida to'xtaydi, matn vositalari skanerlanmaydi ---
    ("feat", "docker exec test git push --no-verify", True),
    ("main", "docker compose run test git commit -m x", True),
    ("feat", "script -q file git push -f", True),
    ("feat", "echo 'git push origin main' | python3 x.py tools/bash", False),
    ("feat", "tool /git push --force", False),
    ("feat", "xargs /usr/bin/grep -l 'git push origin main'", False),
    ("feat", "sed -i 's|; git push -f||' f", False),
    ("feat", "sed -i '/make ci && git push origin main /d' README.md", False),
    ("feat", "perl -pi -e 's/cd x && git push origin main;//' f", False),
    ("feat", "awk '/; git push -f /' f", False),
    ("feat", "gawk '/; git push -f /' f", False),
    ("feat", "rg 'cd x && git push -f'", False),
    ("feat", "xargs rg 'git push -f'", False),
    ("feat", "xargs egrep 'git push -f'", False),
    ("feat", "xargs fgrep 'git push -f'", False),
    ("feat", "xargs ag 'git push -f'", False),
    ("feat", "xargs ack 'git push -f'", False),
    ("feat", "xargs echo 'git push -f'", False),
    ("feat", "xargs printf 'git push -f'", False),
    ("feat", "flock /tmp/l -c 'cd x && gh pr merge 1 --admin'", True),
    ("feat", "flock /tmp/l -c 'cd x && command git push -f'", True),
    ("feat", "flock /tmp/l -c 'cd x && FOO=1 git push -f'", True),
    ("feat", "flock /tmp/l -c 'cd x\ngit push -f'", True),
    ("feat", "git config remove-section core", True),
    ("feat", "git config rename-section core x", True),
    ("feat", "git config remove-section alias", False),
    # --- kalit so'zdan keyingi cd ham kuzatiladi ---
    ("feat", "if cd @MAIN@; then git commit -m x; fi", True),
    ("feat", "if false; then :; elif cd @MAIN@; then git commit -m x; fi", True),
    ("feat", "if false; then :; else cd @MAIN@; git commit -m x; fi", True),
    ("feat", "while cd @MAIN@; do git commit -m x; done", True),
    ("feat", "until cd @MAIN@; do git commit -m x; done", True),
    ("feat", "for i in 1; do cd @MAIN@; git commit -m x; done", True),
    ("feat", "! cd @MAIN@; git commit -m x", True),
]


class HookTableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="guard-hook-")).resolve()
        cls.repos = {"main": cls.tmp / "main", "feat": cls.tmp / "feat", "plain": cls.tmp / "plain"}
        cls.repos["plain"].mkdir()
        for key in ("main", "feat"):
            git(cls.tmp, "init", "-q", "-b", "main", str(cls.repos[key]))
            commit(cls.repos[key], "a.txt")
        git(cls.repos["feat"], "switch", "-q", "-c", "feature")
        git(cls.repos["main"], "branch", "feature")  # main dagi repoda checkout feature ishlashi uchun
        git(cls.repos["feat"], "branch", "topic")

        def run(case):
            repo, cmd, _ = case
            cmd = cmd.replace("@MAIN@", str(cls.repos["main"])).replace("@FEAT@", str(cls.repos["feat"]))
            return run_hook(cmd, cls.repos[repo])

        with ThreadPoolExecutor(max_workers=8) as pool:
            cls.results = list(pool.map(run, CASES))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_table(self):
        self.assertGreaterEqual(len(CASES), 35)
        for (repo, cmd, blocked), r in zip(CASES, self.results):
            with self.subTest(repo=repo, cmd=cmd):
                self.assertEqual(r.returncode, 2 if blocked else 0, r.stderr)
                if blocked:
                    self.assertTrue(r.stderr.startswith("Git buyrug'i bloklandi: "), r.stderr)
                    self.assertIn("Qoida: loyiha CLAUDE.md dagi merge qoidasi", r.stderr)
                else:
                    self.assertEqual(r.stderr, "")

    def test_table_has_allowed_and_blocked_cases_for_every_repo(self):
        for repo in ("main", "feat", "plain"):
            self.assertEqual({b for r, _, b in CASES if r == repo}, {True, False}, repo)

    def test_falls_back_to_claude_project_dir(self):
        r = run_hook("git commit -m x", None, env_extra={"CLAUDE_PROJECT_DIR": str(self.repos["main"])})
        self.assertEqual(r.returncode, 2, r.stderr)
        r = run_hook("git commit -m x", None, env_extra={"CLAUDE_PROJECT_DIR": str(self.repos["feat"])})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_broken_input_never_blocks(self):
        for raw in ("", "not json", "{}", json.dumps({"tool_input": {}}), json.dumps({"tool_input": {"command": ""}})):
            with self.subTest(raw=raw):
                self.assertEqual(run_hook("", None, raw_stdin=raw).returncode, 0)


class PrePushTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="guard-push-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        seed, self.origin, self.work = self.tmp / "seed", self.tmp / "origin.git", self.tmp / "work"
        git(self.tmp, "init", "-q", "-b", "main", str(seed))
        commit(seed, "a.txt")
        git(self.tmp, "init", "-q", "--bare", "-b", "main", str(self.origin))
        git(seed, "push", "-q", str(self.origin), "main")  # seed da hook yoq
        git(self.tmp, "clone", "-q", str(self.origin), str(self.work))
        git(self.work, "config", "core.hooksPath", str(PRE_PUSH.parent))  # faqat vaqtinchalik klonda

    def remote(self, ref):
        r = git(self.origin, "rev-parse", "--verify", "-q", ref, check=False)
        return r.stdout.strip() or None

    def push(self, *args):
        return git(self.work, "push", *args, check=False)

    def test_hook_is_executable_and_valid_bash(self):
        self.assertTrue(os.access(PRE_PUSH, os.X_OK))
        self.assertEqual(subprocess.run(["bash", "-n", str(PRE_PUSH)]).returncode, 0)

    def test_feature_push_ok(self):
        git(self.work, "switch", "-q", "-c", "feature")
        sha = commit(self.work, "b.txt")
        r = self.push("-u", "origin", "feature")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.remote("refs/heads/feature"), sha)

    def test_fast_forward_ok(self):
        git(self.work, "switch", "-q", "-c", "feature")
        commit(self.work, "b.txt")
        self.assertEqual(self.push("origin", "feature").returncode, 0)
        sha = commit(self.work, "c.txt")
        r = self.push("origin", "feature")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.remote("refs/heads/feature"), sha)

    def test_main_push_rejected(self):
        before = self.remote("refs/heads/main")
        commit(self.work, "b.txt")
        r = self.push("origin", "main")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("to'g'ridan-to'g'ri push taqiqlangan", r.stderr)
        self.assertEqual(self.remote("refs/heads/main"), before)

    def test_head_to_main_and_master_rejected(self):
        git(self.work, "switch", "-q", "-c", "feature")
        commit(self.work, "b.txt")
        for target in ("main", "master"):
            with self.subTest(target=target):
                r = self.push("origin", f"HEAD:{target}")
                self.assertNotEqual(r.returncode, 0)
                self.assertIsNone(self.remote("refs/heads/master"))

    def test_main_delete_rejected(self):
        r = self.push("origin", "--delete", "main")
        self.assertNotEqual(r.returncode, 0)
        self.assertIsNotNone(self.remote("refs/heads/main"))

    def test_non_fast_forward_rejected(self):
        git(self.work, "switch", "-q", "-c", "feature")
        commit(self.work, "b.txt")
        first = commit(self.work, "c.txt")
        self.assertEqual(self.push("origin", "feature").returncode, 0)
        git(self.work, "reset", "-q", "--hard", "HEAD~1")
        commit(self.work, "d.txt")
        r = self.push("--force", "origin", "feature")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("fast-forward emas", r.stderr)
        self.assertEqual(self.remote("refs/heads/feature"), first)

    def test_branch_delete_ok(self):
        git(self.work, "switch", "-q", "-c", "feature")
        commit(self.work, "b.txt")
        self.assertEqual(self.push("origin", "feature").returncode, 0)
        r = self.push("origin", "--delete", "feature")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIsNone(self.remote("refs/heads/feature"))

    def run_hook_lines(self, lines):
        return subprocess.run([str(PRE_PUSH)], cwd=self.work, input="\n".join(lines) + "\n", capture_output=True, text=True, env=ENV)

    def test_remote_commit_missing_locally_asks_for_fetch(self):
        sha = git(self.work, "rev-parse", "HEAD").stdout.strip()
        r = self.run_hook_lines([f"refs/heads/f {sha} refs/heads/f {'1' * 40}"])
        self.assertEqual(r.returncode, 1)
        self.assertIn("git fetch", r.stderr)

    def test_all_lines_are_processed(self):
        sha = git(self.work, "rev-parse", "HEAD").stdout.strip()
        zero = "0" * 40
        ok = f"refs/heads/f {sha} refs/heads/f {zero}"
        bad = f"refs/heads/f {sha} refs/heads/main {sha}"
        for lines in ([ok, bad], [bad, ok]):
            with self.subTest(order=lines[0] == ok):
                r = self.run_hook_lines(lines)
                self.assertEqual(r.returncode, 1)
                self.assertEqual(r.stderr.count("push rad etildi"), 1, r.stderr)
        delete = f"(delete) {zero} refs/heads/old {sha}"
        self.assertEqual(self.run_hook_lines([ok, delete]).returncode, 0)


class PrePushProtectedTests(unittest.TestCase):
    """quality.protected: pre-push ning himoyalangan branchlari (standart main va master)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="guard-prot-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        seed, self.origin, self.work = self.tmp / "seed", self.tmp / "origin.git", self.tmp / "work"
        git(self.tmp, "init", "-q", "-b", "main", str(seed))
        commit(seed, "a.txt")
        git(self.tmp, "init", "-q", "--bare", "-b", "main", str(self.origin))
        git(seed, "push", "-q", str(self.origin), "main")
        git(self.tmp, "clone", "-q", str(self.origin), str(self.work))
        git(self.work, "config", "core.hooksPath", str(PRE_PUSH.parent))

    def remote(self, ref):
        return git(self.origin, "rev-parse", "--verify", "-q", ref, check=False).stdout.strip() or None

    def push(self, *args):
        return git(self.work, "push", *args, check=False)

    def test_custom_list_protects_develop_and_frees_main(self):
        git(self.work, "config", "quality.protected", "develop")
        git(self.work, "switch", "-q", "-c", "develop")
        commit(self.work, "d.txt")
        r = self.push("origin", "develop")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("refs/heads/develop ga to'g'ridan-to'g'ri push taqiqlangan", r.stderr)
        self.assertIsNone(self.remote("refs/heads/develop"))
        git(self.work, "switch", "-q", "main")
        sha = commit(self.work, "m.txt")
        r = self.push("origin", "main")  # ro'yxatda yo'q: o'rniga qo'yiladi, standart emas
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.remote("refs/heads/main"), sha)

    def test_multi_valued_and_comma_forms(self):
        for setup in (("--add", "quality.protected", "main"), ("--add", "quality.protected", "develop")), (("quality.protected", "main, develop"),):
            with self.subTest(setup=setup):
                git(self.work, "config", "--unset-all", "quality.protected", check=False)
                for args in setup:
                    git(self.work, "config", *args)
                git(self.work, "switch", "-q", "-C", "tmp", "main")
                commit(self.work, f"x{len(setup)}.txt")
                for target in ("main", "develop"):
                    r = self.push("origin", f"HEAD:{target}")
                    self.assertNotEqual(r.returncode, 0, target)
                    self.assertIsNone(self.remote("refs/heads/develop"))
                git(self.work, "reset", "-q", "--hard", "origin/main")

    def test_unset_means_main_and_master(self):
        git(self.work, "switch", "-q", "-c", "feature")
        commit(self.work, "b.txt")
        for target in ("main", "master"):
            self.assertNotEqual(self.push("origin", f"HEAD:{target}").returncode, 0, target)
        self.assertEqual(self.push("origin", "HEAD:develop").returncode, 0)


class MainGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="guard-main-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = self.tmp / "repo"
        git(self.tmp, "init", "-q", "-b", "main", str(self.repo))
        self.baseline = commit(self.repo, "base.txt")

    def pr_merge(self, n, subject=None, committer=None):
        git(self.repo, "switch", "-q", "-c", f"pr{n}")
        commit(self.repo, f"pr{n}.txt")
        git(self.repo, "switch", "-q", "main")
        git(self.repo, "merge", "-q", "--no-ff", "-m", subject or f"Merge pull request #{n} from org/pr{n}", f"pr{n}", env_extra=GITHUB if committer is None else committer)

    def squash(self, n, subject=None, committer=None):
        """Squash merge: 1 ota-onali commit, mavzu "... (#N)"; committer standart GitHub."""
        return commit(self.repo, f"sq{n}.txt", subject or f"Funksiya {n} (#{n})", env_extra=GITHUB if committer is None else committer)

    def hooks_on(self, executable=True):
        hook = self.repo / ".githooks" / "pre-push"
        hook.parent.mkdir(exist_ok=True)
        hook.write_text("#!/bin/sh\nexit 0\n")
        hook.chmod(0o755 if executable else 0o644)
        git(self.repo, "config", "core.hooksPath", ".githooks")

    def write_config(self, **cfg):
        path = self.repo / ".claude" / "quality" / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cfg))

    def run_guard(self, *args, cwd=None):
        return subprocess.run([sys.executable, str(MAIN_GUARD), *args], capture_output=True, text=True, env=ENV, timeout=60,
                              cwd=str(cwd or self.tmp))

    def guard(self, *args):
        return self.run_guard("--repo", str(self.repo), "--ref", "main", "--baseline", self.baseline, *args)

    def add_submodule(self, path, in_repo=None, name=None):
        """Haqiqiy submodule (file protokoli ruxsat bilan); commit qilinmaydi, shuning uchun main tarixi o'zgarmaydi."""
        repo = in_repo or self.repo
        src = self.tmp / f"src-{name or path.replace('/', '-')}"
        if not src.exists():
            git(self.tmp, "init", "-q", "-b", "main", str(src))
            commit(src, "s.txt")
        git(repo, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(src), path)
        return repo / path

    def test_pr_merges_only_is_clean_and_silent(self):
        self.pr_merge(1)
        self.pr_merge(2)
        r = self.guard("--no-hooks-check")
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))

    def test_empty_history_after_baseline_is_clean(self):
        self.assertEqual(self.guard("--no-hooks-check").returncode, 0)

    def test_verbose_reports_success(self):
        self.pr_merge(1)
        r = self.guard("--no-hooks-check", "--verbose")
        self.assertEqual(r.returncode, 0)
        self.assertIn("tarix toza", r.stdout)

    def test_subject_without_from_fails(self):
        self.pr_merge(1, "Merge pull request #1: sarlavha")
        self.assertEqual(self.guard("--no-hooks-check").returncode, 1)

    def test_direct_commit_on_main_fails_with_sha_and_subject(self):
        self.pr_merge(1)
        sha = commit(self.repo, "direct.txt")
        r = self.guard("--no-hooks-check")
        self.assertEqual(r.returncode, 1)
        self.assertIn(sha[:7], r.stderr)
        self.assertIn("direct.txt", r.stderr)

    def test_merge_with_other_subject_fails(self):
        self.pr_merge(1, "Merge branch 'pr1' into main")
        r = self.guard("--no-hooks-check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("Merge branch 'pr1' into main", r.stderr)

    def test_one_parent_commit_with_pr_subject_fails(self):
        commit(self.repo, "x.txt", "Merge pull request #9 from org/x", env_extra=GITHUB)
        self.assertEqual(self.guard("--no-hooks-check").returncode, 1)

    # --- merge_style va committer sharti ---
    def test_merge_style_defaults_to_merge_and_rejects_squash_history(self):
        self.squash(1)
        self.squash(2)
        r = self.guard("--no-hooks-check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("merge_style=merge", r.stderr)
        self.assertIn("Funksiya 2 (#2)", r.stderr)

    def test_squash_history_passes_under_squash_and_any(self):
        self.squash(1)
        self.squash(2)
        for style in ("squash", "any"):
            with self.subTest(style=style):
                r = self.guard("--no-hooks-check", "--merge-style", style)
                self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))

    def test_merge_style_from_config_json(self):
        self.write_config(ref="main", baseline=self.baseline, merge_style="squash")
        self.squash(1)
        self.assertEqual(self.run_guard("--repo", str(self.repo), "--no-hooks-check").returncode, 0)
        self.pr_merge(2)  # squash rejimida 2 ota-onali merge o'tmaydi
        self.assertEqual(self.run_guard("--repo", str(self.repo), "--no-hooks-check").returncode, 1)
        self.assertEqual(self.run_guard("--repo", str(self.repo), "--no-hooks-check", "--merge-style", "any").returncode, 0)

    def test_local_commit_with_pr_number_but_user_committer_fails_under_squash(self):
        sha = self.squash(12, "Funksiya (#12)", committer={})  # committer = foydalanuvchi (t@example.com)
        for style in ("squash", "any"):
            with self.subTest(style=style):
                r = self.guard("--no-hooks-check", "--merge-style", style)
                self.assertEqual(r.returncode, 1)
                self.assertIn(sha[:7], r.stderr)
                self.assertIn("committer t@example.com", r.stderr)

    def test_local_merge_with_pr_subject_but_user_committer_fails_under_merge(self):
        self.pr_merge(1, committer={})  # qo'lda yasalgan "Merge pull request" mavzusi
        for style in ("merge", "any"):
            with self.subTest(style=style):
                self.assertEqual(self.guard("--no-hooks-check", "--merge-style", style).returncode, 1)

    def test_real_style_merge_passes_under_any_and_merge_only(self):
        self.pr_merge(1)
        self.squash(2)
        self.pr_merge(3)
        self.assertEqual(self.guard("--no-hooks-check", "--merge-style", "any").returncode, 0)
        self.assertEqual(self.guard("--no-hooks-check", "--merge-style", "merge").returncode, 1)  # squash commit
        self.assertEqual(self.guard("--no-hooks-check", "--merge-style", "squash").returncode, 1)  # 2 ota-onali merge

    def test_squash_subject_must_end_with_pr_number_and_have_one_parent(self):
        for subject in ("Funksiya (#12) qo'shimcha", "Funksiya #12", "Funksiya (#x)", "Funksiya (#)"):
            with self.subTest(subject=subject):
                git(self.repo, "reset", "-q", "--hard", self.baseline)
                self.squash(1, subject)
                self.assertEqual(self.guard("--no-hooks-check", "--merge-style", "squash").returncode, 1)
        git(self.repo, "reset", "-q", "--hard", self.baseline)
        self.pr_merge(1, "Merge branch 'x' (#1)")  # 2 ota-onali: squash qoidasi o'tmaydi
        self.assertEqual(self.guard("--no-hooks-check", "--merge-style", "any").returncode, 1)

    def test_invalid_merge_style_in_config_is_reported(self):
        self.write_config(ref="main", baseline=self.baseline, merge_style="rebase")
        r = self.run_guard("--repo", str(self.repo), "--no-hooks-check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("merge_style", r.stderr)

    def test_commits_before_baseline_are_ignored(self):
        git(self.repo, "reset", "-q", "--hard", "HEAD")  # baseline allaqachon HEAD
        self.assertEqual(self.guard("--no-hooks-check").returncode, 0)

    def test_baseline_not_in_repo_fails_clearly(self):
        r = self.guard("--no-hooks-check", "--baseline", "f" * 40)
        self.assertEqual(r.returncode, 1)
        self.assertIn("baseline", r.stderr)

    def test_missing_baseline_fails_clearly(self):
        r = self.run_guard("--repo", str(self.repo), "--ref", "main", "--no-hooks-check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("baseline ko'rsatilmagan", r.stderr)
        self.assertIn("config.json", r.stderr)

    def test_missing_baseline_with_warn_only_goes_to_stdout(self):
        r = self.run_guard("--repo", str(self.repo), "--ref", "main", "--no-hooks-check", "--warn-only")
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        self.assertIn("baseline ko'rsatilmagan", r.stdout)

    def test_baseline_not_ancestor_fails(self):
        git(self.repo, "switch", "-q", "-c", "side")
        side = commit(self.repo, "side.txt")
        git(self.repo, "switch", "-q", "main")
        r = self.guard("--no-hooks-check", "--baseline", side)
        self.assertEqual(r.returncode, 1)
        self.assertIn("tarixida emas", r.stderr)

    def test_missing_ref_fails(self):
        r = self.guard("--no-hooks-check", "--ref", "origin/main")
        self.assertEqual(r.returncode, 1)
        self.assertIn("topilmadi", r.stderr)

    def test_config_json_supplies_ref_and_baseline_and_flags_win(self):
        self.write_config(ref="main", baseline=self.baseline)
        self.pr_merge(1)
        r = self.run_guard("--repo", str(self.repo), "--no-hooks-check", "--verbose")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(self.baseline[:7], r.stdout)
        sha = commit(self.repo, "direct.txt")
        self.assertEqual(self.run_guard("--repo", str(self.repo), "--no-hooks-check").returncode, 1)
        r = self.run_guard("--repo", str(self.repo), "--no-hooks-check", "--baseline", sha)  # bayroq config dan ustun
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_repo_defaults_to_git_toplevel_of_cwd(self):
        self.write_config(ref="main", baseline=self.baseline)
        sub = self.repo / "deep" / "dir"
        sub.mkdir(parents=True)
        commit(self.repo, "direct.txt")
        r = self.run_guard("--no-hooks-check", cwd=sub)
        self.assertEqual(r.returncode, 1)
        self.assertIn("direct.txt", r.stderr)

    def test_broken_config_json_is_reported(self):
        path = self.repo / ".claude" / "quality" / "config.json"
        path.parent.mkdir(parents=True)
        path.write_text("{not json")
        r = self.guard("--no-hooks-check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("config.json", r.stderr)

    def test_fetch_uses_remote_and_branch_of_ref(self):
        origin = self.tmp / "origin.git"
        git(self.tmp, "init", "-q", "--bare", "-b", "main", str(origin))
        git(self.repo, "remote", "add", "origin", str(origin))
        git(self.repo, "push", "-q", "origin", "main")
        self.pr_merge(1)
        git(self.repo, "push", "-q", "origin", "main")
        git(self.repo, "update-ref", "-d", "refs/remotes/origin/main")
        r = self.run_guard("--repo", str(self.repo), "--ref", "origin/main", "--baseline", self.baseline, "--no-hooks-check", "--fetch")
        self.assertEqual((r.returncode, r.stderr), (0, ""))

    def test_hooks_path_unset_fails_then_set_passes(self):
        r = self.guard()
        self.assertEqual(r.returncode, 1)
        self.assertIn("core.hooksPath", r.stderr)
        self.hooks_on()
        self.assertEqual(self.guard().returncode, 0)

    def test_no_hooks_check_skips_hooks(self):
        self.assertEqual(self.guard("--no-hooks-check").returncode, 0)

    def test_non_executable_pre_push_fails(self):
        self.hooks_on(executable=False)
        r = self.guard()
        self.assertEqual(r.returncode, 1)
        self.assertIn("pre-push", r.stderr)

    def test_wrong_hooks_path_fails(self):
        self.hooks_on()
        git(self.repo, "config", "core.hooksPath", "/dev/null")
        self.assertEqual(self.guard().returncode, 1)

    def test_submodule_hooks_path_is_relative_to_repo_hooks(self):
        self.hooks_on()
        sub = self.add_submodule("docs/arxitektura")
        r = self.guard()
        self.assertEqual(r.returncode, 1)
        self.assertIn("docs/arxitektura", r.stderr)
        self.assertIn("kutilgan ../../.githooks", r.stderr)
        git(sub, "config", "core.hooksPath", "../../.githooks")
        self.assertEqual(self.guard().returncode, 0)

    def test_nested_submodule_path_expects_more_dots(self):
        self.hooks_on()
        sub = self.add_submodule("vendor/libs/x")
        git(sub, "config", "core.hooksPath", "../../.githooks")  # chuqurlikka mos emas
        r = self.guard()
        self.assertEqual(r.returncode, 1)
        self.assertIn("kutilgan ../../../.githooks", r.stderr)
        git(sub, "config", "core.hooksPath", "../../../.githooks")
        self.assertEqual(self.guard().returncode, 0)

    def test_submodule_inside_submodule_is_checked(self):
        self.hooks_on()
        inner_src = self.tmp / "src-inner"
        git(self.tmp, "init", "-q", "-b", "main", str(inner_src))
        commit(inner_src, "i.txt")
        outer_src = self.tmp / "src-outer"
        git(self.tmp, "init", "-q", "-b", "main", str(outer_src))
        commit(outer_src, "o.txt")
        git(outer_src, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(inner_src), "lib")
        git(outer_src, "commit", "-q", "-m", "inner")
        outer = self.add_submodule("ext/outer", name="outer")
        git(self.repo, "-c", "protocol.file.allow=always", "submodule", "update", "-q", "--init", "--recursive")
        self.assertTrue((outer / "lib" / ".git").exists())
        r = self.guard()
        self.assertEqual(r.returncode, 1)
        self.assertIn("ext/outer da", r.stderr)
        self.assertIn("ext/outer/lib da", r.stderr)
        self.assertIn("kutilgan ../../../.githooks", r.stderr)  # ext/outer/lib 3 daraja chuqur
        git(outer, "config", "core.hooksPath", "../../.githooks")
        git(outer / "lib", "config", "core.hooksPath", "../../../.githooks")
        self.assertEqual(self.guard().returncode, 0)

    def test_uninitialized_submodule_is_skipped(self):
        self.hooks_on()
        (self.repo / ".gitmodules").write_text('[submodule "x"]\n\tpath = docs/x\n\turl = ./x\n')
        (self.repo / "docs" / "x").mkdir(parents=True)  # bo'sh papka = init qilinmagan
        self.assertEqual(self.guard().returncode, 0)

    def test_protected_mismatch_with_pre_push_is_reported_and_setup_fixes_it(self):
        self.hooks_on()
        self.write_config(ref="main", baseline=self.baseline, protected=["develop", "main"])
        r = self.run_guard("--repo", str(self.repo))
        self.assertEqual(r.returncode, 1)
        self.assertIn("develop,main", r.stderr)
        r = self.run_guard("--repo", str(self.repo), "--setup")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(git(self.repo, "config", "quality.protected").stdout.strip(), "develop,main")

    def test_setup_enables_hooks_everywhere(self):
        (self.repo / ".githooks").mkdir()
        hook = self.repo / ".githooks" / "pre-push"
        hook.write_text("#!/bin/sh\nexit 0\n")
        hook.chmod(0o644)
        sub = self.add_submodule("vendor/libs/x")
        self.write_config(ref="main", baseline=self.baseline)
        self.assertEqual(self.run_guard("--repo", str(self.repo)).returncode, 1)
        r = self.run_guard("--repo", str(self.repo), "--setup")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(git(self.repo, "config", "core.hooksPath").stdout.strip(), ".githooks")
        self.assertEqual(git(sub, "config", "core.hooksPath").stdout.strip(), "../../../.githooks")
        self.assertTrue(os.access(hook, os.X_OK))

    def test_warn_only_prints_to_stdout_and_exits_zero(self):
        r = self.guard("--warn-only")
        self.assertEqual(r.returncode, 0)
        self.assertIn("core.hooksPath", r.stdout)
        self.assertEqual(r.stderr, "")

    def test_fetch_failure_only_warns(self):
        self.hooks_on()
        r = self.guard("--fetch")
        self.assertEqual(r.returncode, 0)
        self.assertIn("ogohlantirish", r.stderr)


class HookProtectedConfigTests(unittest.TestCase):
    """guard-git.py: himoyalangan branchlar .claude/quality/config.json dan (repo ildizi, keyin $CLAUDE_PROJECT_DIR)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="guard-cfg-")).resolve()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def repo(self, name, branch, protected=None, raw_config=None):
        path = self.tmp / name
        git(self.tmp, "init", "-q", "-b", "main", str(path))
        commit(path, "a.txt")
        git(path, "branch", "develop")
        git(path, "branch", "topic")
        if branch != "main":
            git(path, "switch", "-q", branch)
        if protected is not None or raw_config is not None:
            cfg = path / ".claude" / "quality" / "config.json"
            cfg.parent.mkdir(parents=True, exist_ok=True)
            cfg.write_text(raw_config if raw_config is not None else json.dumps({"protected": protected}))
        return path

    def check(self, cwd, command, blocked, **kw):
        r = run_hook(command, cwd, **kw)
        self.assertEqual(r.returncode, 2 if blocked else 0, f"{command}: {r.stderr}")

    def test_custom_list_replaces_default(self):
        topic = self.repo("c-topic", "topic", ["develop"])
        for cmd, blocked in (("git push origin develop", True), ("git push origin HEAD:refs/heads/develop", True),
                             ("git push origin :develop", True), ("git push origin main", False), ("git push origin master", False),
                             ("git push origin topic", False), ("git push", False)):
            with self.subTest(cmd=cmd):
                self.check(topic, cmd, blocked)

    def test_commit_blocked_only_on_listed_branch(self):
        on_develop = self.repo("c-develop", "develop", ["develop"])
        on_main = self.repo("c-main", "main", ["develop"])
        self.check(on_develop, "git commit -m x", True)
        self.check(on_develop, "git push", True)
        self.check(on_main, "git commit -m x", False)
        self.check(on_main, "git push", False)
        self.check(on_main, "git switch develop && git commit -m x", True)
        self.check(on_main, "git checkout develop && git push", True)

    def test_several_branches(self):
        topic = self.repo("c-several", "topic", ["main", "release", "develop"])
        for target in ("main", "release", "develop"):
            self.check(topic, f"git push origin {target}", True)
        self.check(topic, "git push origin topic", False)
        self.check(topic, 'git push origin release "x', True)  # tirnoq juftlanmagan: xom matn tekshiruvi ham ro'yxatni biladi

    def test_config_is_looked_up_from_target_repo_of_dash_c(self):
        topic = self.repo("c-dashc", "topic", ["develop"])
        plain = self.tmp / "plain-dashc"
        plain.mkdir()
        self.check(plain, f"git -C {topic} push origin develop", True)
        self.check(plain, f"cd {topic} && git push origin develop", True)
        self.check(plain, f"git -C {topic} push origin main", False)

    def test_falls_back_to_claude_project_dir_config(self):
        project = self.repo("c-project", "topic", ["develop"])
        elsewhere = self.repo("c-elsewhere", "topic")  # configsiz repo
        self.check(elsewhere, "git push origin develop", True, env_extra={"CLAUDE_PROJECT_DIR": str(project)})
        self.check(elsewhere, "git push origin develop", False)
        self.check(elsewhere, "git push origin main", False, env_extra={"CLAUDE_PROJECT_DIR": str(project)})

    def test_repo_config_wins_over_claude_project_dir(self):
        project = self.repo("c-wins-project", "topic", ["develop"])
        own = self.repo("c-wins-own", "topic", ["release"])
        self.check(own, "git push origin release", True, env_extra={"CLAUDE_PROJECT_DIR": str(project)})
        self.check(own, "git push origin develop", False, env_extra={"CLAUDE_PROJECT_DIR": str(project)})

    def test_unusable_config_falls_back_to_main_and_master(self):
        for name, raw in (("garbage", "{not json"), ("empty", json.dumps({"protected": []})), ("wrong-type", json.dumps({"protected": "develop"})),
                          ("no-key", json.dumps({"ref": "origin/main"})), ("not-object", "[1]"), ("mixed", json.dumps({"protected": ["x", 5]}))):
            with self.subTest(config=name):
                topic = self.repo(f"c-bad-{name}", "topic", raw_config=raw)
                self.check(topic, "git push origin main", True)
                self.check(topic, "git push origin master", True)
                self.check(topic, "git push origin develop", False)


if __name__ == "__main__":
    unittest.main()
