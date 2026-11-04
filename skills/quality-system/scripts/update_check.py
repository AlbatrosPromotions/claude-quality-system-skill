#!/usr/bin/env python3
"""Update check of the quality-system skill: a machine that installed the skill from the install repo learns when a newer version is published.

  update_check.py --installed <clone dir> [--flags "--hooks"]   install.sh: record what was installed from that git checkout
                                                                -> $CLAUDE_CONFIG_DIR/quality-system-install.json
  update_check.py --check                                       the background worker: compare the remote HEAD with the installed
                                                                commit -> $CLAUDE_CONFIG_DIR/quality-system-update.json
  update_check.py --notice                                      print the update line for the lead, if there is one
  update_check.py --hook                                        SessionStart hook: reads the event JSON on stdin; on source startup/clear
                                                                prints {"hookSpecificOutput": {...additionalContext}} when the remote is
                                                                ahead, nothing otherwise; always exit 0

The notice starts `--check` detached when the last check is older than a day (an hour after a failure). The check asks the remote for its
HEAD with `git ls-remote` (the same deploy key the install used) and, when the clone is still there, fetches it to list the new commits.
The hook itself never touches the network: it reads the last result, so a newer version shows up at the next session start. A result is
trusted only for the install it was made for ("for" == installed commit).

Nothing here raises into the hook. Without quality-system-install.json (the machine that is the source of truth, or an install from a
plain directory) nothing happens. QUALITY_SYSTEM_UPDATE_CHECK=0 turns the check off; so does deleting quality-system-install.json.
"""
import json
import os
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone

KIT = "quality-system"
CONFIG = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
INSTALL = os.path.join(CONFIG, KIT + "-install.json")
UPDATE = os.path.join(CONFIG, KIT + "-update.json")
OFF_ENV = "QUALITY_SYSTEM_UPDATE_CHECK"
DEFAULT_DIR = "~/claude-quality-system-skill"  # shown when the record has no repo_dir
EVERY = 24 * 3600  # seconds between two checks
RETRY = 3600  # after a check that failed (no network, no key)
SHOW = 5  # new commits listed in the notice
STAMP = "%Y-%m-%dT%H:%M:%SZ"


class GitError(Exception):
    pass


def now():
    return datetime.now(timezone.utc).strftime(STAMP)


def age(stamp):
    """Seconds since an ISO stamp written by now(); None when it is unreadable (then the check counts as never done)."""
    try:
        return time.time() - datetime.strptime(str(stamp), STAMP).replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def read(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write(path, data):
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def git(args, cwd=None, timeout=20):
    """stdout of a git command that must succeed; GitError with the last stderr line otherwise. Never prompts, never hangs."""
    try:
        p = subprocess.run(["git"] + args, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True, encoding="utf-8",
                           errors="replace", timeout=timeout, env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
    except OSError as e:
        raise GitError("git: %s" % e)
    except subprocess.TimeoutExpired:
        raise GitError("git %s: no answer in %d s" % (args[0], timeout))
    if p.returncode:
        detail = (p.stderr.strip() or "exit %d" % p.returncode).splitlines()[-1]
        raise GitError("git %s: %s" % (args[0], detail))
    return p.stdout.strip()


def record_install(repo_dir, flags=""):
    """Write quality-system-install.json for an install from the checkout repo_dir; a directory that is not one removes the record."""
    repo_dir = os.path.abspath(repo_dir)
    try:
        top = git(["rev-parse", "--show-toplevel"], cwd=repo_dir)
        if os.path.realpath(top) != os.path.realpath(repo_dir):
            raise GitError("%s is inside the checkout %s, not a checkout itself" % (repo_dir, top))
        commit = git(["rev-parse", "HEAD"], cwd=repo_dir)
        commit_date = git(["log", "-1", "--format=%cd", "--date=short", "HEAD"], cwd=repo_dir)
        remote = git(["remote", "get-url", "origin"], cwd=repo_dir)
    except GitError as e:
        if os.path.exists(INSTALL):
            os.remove(INSTALL)
        return "update checks are off: %s" % e
    os.makedirs(CONFIG, exist_ok=True)
    write(INSTALL, {"commit": commit, "commit_date": commit_date, "remote": remote, "repo_dir": repo_dir,
                    "flags": " ".join(str(flags).split()), "installed_at": now()})
    return "recorded %s (%s) from %s; a newer version will be offered at session start" % (commit[:7], commit_date, remote)


def installed():
    data = read(INSTALL)
    return data if data and data.get("commit") and data.get("remote") else None


def check():
    """The background worker: the remote HEAD, and the commits between the installed one and it when the clone is still there."""
    inst = installed()
    if not inst:
        return
    result = {"for": inst["commit"], "checked_at": now(), "latest": None, "latest_date": None, "log": [], "error": None}
    try:
        head = [line for line in git(["ls-remote", inst["remote"], "HEAD"], timeout=30).splitlines() if line.endswith("HEAD")]
        latest = head[0].split()[0] if head and head[0].split() else ""
        if len(latest) < 7:
            raise GitError("git ls-remote: the remote has no HEAD")
        result["latest"] = latest
        repo = inst.get("repo_dir") or ""
        if latest != inst["commit"] and os.path.isdir(repo):
            try:
                git(["fetch", "--quiet", "origin"], cwd=repo, timeout=60)
                result["latest_date"] = git(["log", "-1", "--format=%cd", "--date=short", latest], cwd=repo)
                log = git(["log", "-n", "20", "--format=%h %cd %s", "--date=short", "%s..%s" % (inst["commit"], latest)], cwd=repo)
                result["log"] = log.splitlines()
            except GitError as e:
                result["log_error"] = str(e)  # the notice still works with the commit ids alone
    except GitError as e:
        result["error"] = str(e)
    write(UPDATE, result)


def spawn_check():
    """Start `--check` detached: no pipe to the hook, its own session, so the hook returns at once and the check outlives it."""
    try:
        subprocess.Popen([sys.executable, os.path.abspath(__file__), "--check"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
    except OSError:
        pass


def notice():
    """The update line for the lead, or None. Starts a background check when there is no fresh result for this install."""
    if os.environ.get(OFF_ENV) == "0":
        return None
    inst = installed()
    if not inst:
        return None
    last = read(UPDATE)
    if last and last.get("for") != inst["commit"]:
        last = None  # made for another install: stale
    since = age(last.get("checked_at")) if last else None
    if since is None or since > (RETRY if last.get("error") else EVERY):
        spawn_check()
    if not last or not last.get("latest") or last["latest"] == inst["commit"]:
        return None
    where = shlex.quote(inst["repo_dir"]) if inst.get("repo_dir") else DEFAULT_DIR  # a quoted ~ would not expand
    command = "git -C %s pull && bash %s/install.sh" % (where, where)
    if inst.get("flags"):
        command += " " + inst["flags"]
    lines = ["%s: a newer version of the %s skill is published (installed %s, latest %s). Tell the user in one line at "
             "the start of your reply and offer to update with `%s`; run it only when the user agrees."
             % (KIT, KIT, " ".join(filter(None, (inst["commit"][:7], inst.get("commit_date")))),
                " ".join(filter(None, (last["latest"][:7], last.get("latest_date")))), command)]
    log = [str(line) for line in last.get("log") or []]
    if log:
        lines.append("New commits: " + "; ".join(log[:SHOW]) + (" (and %d more)" % (len(log) - SHOW) if len(log) > SHOW else ""))
    return "\n".join(lines)


def hook():
    """SessionStart hook: the notice as additionalContext for a new session. A courtesy: whatever goes wrong, the session starts (exit 0)."""
    try:
        event = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace"))
        if not isinstance(event, dict):
            raise ValueError("expected a JSON object on stdin")
        if event.get("source") not in ("startup", "clear"):  # not on resume or compact (the lead already has the note), nor without a source
            return
        text = notice()
        if text:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}))
            sys.stdout.flush()
    except Exception as e:
        print("%s update check: %s: %s" % (KIT, type(e).__name__, " ".join(str(e).split())), file=sys.stderr)


def main(argv):
    if argv[:1] == ["--check"]:
        check()
    elif argv[:1] == ["--installed"] and len(argv) in (2, 4) and (len(argv) == 2 or argv[2] == "--flags"):
        print(record_install(argv[1], argv[3] if len(argv) == 4 else ""))
    elif argv[:1] == ["--notice"]:
        text = notice()
        if text:
            print(text)
    elif argv[:1] == ["--hook"]:
        hook()
    else:
        sys.exit(__doc__.strip())


if __name__ == "__main__":
    main(sys.argv[1:])
