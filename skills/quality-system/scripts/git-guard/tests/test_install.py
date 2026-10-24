#!/usr/bin/env python3
"""install.py testlari (faqat to'plam papkasida; loyihaga ko'chirilmaydi).

Har test o'z vaqtinchalik loyihasini quradi: bare origin, bir nechta PR-uslubidagi merge, ishga tushirilgan submodule va
boshqa hook yozuvi bor oldindan mavjud .claude/settings.json.

  python3 -m unittest discover -s tests -v      (to'plam papkasida)
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
INSTALL = KIT / "install.py"

HOME = tempfile.mkdtemp(prefix="install-home-")
ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_") and k != "CLAUDE_PROJECT_DIR"}
ENV.update(
    HOME=HOME, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1",
    GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com",
)

UNRELATED = {"type": "command", "command": "\"$CLAUDE_PROJECT_DIR\"/.claude/hooks/other.sh", "timeout": 30}
SETTINGS_BEFORE = {
    "permissions": {"allow": ["Bash(ls)"]},
    "hooks": {
        "PreToolUse": [{"matcher": "Bash", "hooks": [UNRELATED]}, {"matcher": "Edit", "hooks": [UNRELATED]}],
        "Stop": [{"hooks": [UNRELATED]}],
    },
}


def tearDownModule():
    shutil.rmtree(HOME, ignore_errors=True)


GITHUB = {"GIT_COMMITTER_EMAIL": "noreply@github.com", "GIT_COMMITTER_NAME": "GitHub"}  # GitHub serveri yaratgan commit committer i


def git(cwd, *args, check=True, env_extra=None):
    r = subprocess.run(["git", "-C", str(cwd), "-c", "protocol.file.allow=always", *args], capture_output=True, text=True,
                       env=dict(ENV, **(env_extra or {})))
    if check and r.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} ({cwd}): {r.stderr}")
    return r


def commit(repo, name, message=None, env_extra=None):
    (Path(repo) / name).parent.mkdir(parents=True, exist_ok=True)
    (Path(repo) / name).write_text(name)
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", message or name, env_extra=env_extra)
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def snapshot(root):
    """Ishchi daraxt (.git siz) fayllari va ularning baytlari."""
    out = {}
    for path in sorted(Path(root).rglob("*")):
        rel = path.relative_to(root)
        if ".git" in rel.parts[:1] or not path.is_file():
            continue
        out[str(rel)] = path.read_bytes()
    return out


def load_install():
    spec = importlib.util.spec_from_file_location("kit_install", INSTALL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="install-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def make_project(self, settings=SETTINGS_BEFORE, with_origin=True):
        """Qaytaradi (proj, origin sha). proj: origin dan klon, ichida docs/arch submodule, 2 ta PR merge."""
        sub_src = self.tmp / "arch-src"
        git(self.tmp, "init", "-q", "-b", "main", str(sub_src))
        commit(sub_src, "s.txt")
        seed = self.tmp / "seed"
        git(self.tmp, "init", "-q", "-b", "main", str(seed))
        commit(seed, "a.txt")
        if settings is not None:
            (seed / ".claude").mkdir()
            (seed / ".claude" / "settings.json").write_text(json.dumps(settings, indent=2) + "\n")
            git(seed, "add", ".claude")
            git(seed, "commit", "-q", "-m", "settings")
        git(seed, "submodule", "add", "-q", str(sub_src), "docs/arch")
        git(seed, "commit", "-q", "-m", "submodule")
        for n in (1, 2):
            git(seed, "switch", "-q", "-c", f"pr{n}")
            commit(seed, f"pr{n}.txt")
            git(seed, "switch", "-q", "main")
            git(seed, "merge", "-q", "--no-ff", "-m", f"Merge pull request #{n} from org/pr{n}", f"pr{n}", env_extra=GITHUB)
        if not with_origin:
            return seed, None
        origin = self.tmp / "origin.git"
        git(self.tmp, "init", "-q", "--bare", "-b", "main", str(origin))
        git(seed, "push", "-q", str(origin), "main")
        proj = self.tmp / "proj"
        git(self.tmp, "clone", "-q", "--recurse-submodules", str(origin), str(proj))
        return proj, git(origin, "rev-parse", "main").stdout.strip()

    def install(self, proj, *args):
        return subprocess.run([sys.executable, str(INSTALL), str(proj), *args], capture_output=True, text=True, env=ENV, timeout=300)

    def settings(self, proj):
        return json.loads((proj / ".claude" / "settings.json").read_text())

    def commands(self, proj, event):
        return [h["command"] for g in self.settings(proj)["hooks"].get(event, []) for h in g["hooks"]]

    def test_dry_run_writes_nothing(self):
        proj, sha = self.make_project()
        before, cfg_before = snapshot(proj), git(proj, "config", "--local", "--list").stdout
        r = self.install(proj, "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("[dry-run]", r.stdout)
        self.assertIn(sha, r.stdout)
        self.assertEqual(snapshot(proj), before)
        self.assertEqual(git(proj, "config", "--local", "--list").stdout, cfg_before)
        self.assertEqual(git(proj, "status", "--porcelain").stdout, "")

    def test_full_install_then_idempotent_rerun(self):
        proj, sha = self.make_project()
        r = self.install(proj)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(sha, r.stdout)
        self.assertIn("tarix toza", r.stdout)
        self.assertRegex(r.stdout, r"Ran \d+ tests")
        for rel in (".claude/hooks/guard-git.py", ".githooks/pre-push", ".claude/quality/main_guard.py"):
            self.assertTrue(os.access(proj / rel, os.X_OK), rel)
        self.assertTrue((proj / ".claude/quality/tests/test_git_guard.py").is_file())
        self.assertEqual(json.loads((proj / ".claude/quality/config.json").read_text()),
                         {"ref": "origin/main", "baseline": sha, "protected": ["main", "master"], "merge_style": "merge"})
        self.assertEqual(git(proj, "config", "core.hooksPath").stdout.strip(), ".githooks")
        self.assertEqual(git(proj / "docs" / "arch", "config", "core.hooksPath").stdout.strip(), "../../.githooks")
        # settings: begona yozuvlar joyida, bittadan yangi yozuv
        data = self.settings(proj)
        self.assertEqual(data["permissions"], SETTINGS_BEFORE["permissions"])
        self.assertEqual(data["hooks"]["Stop"], SETTINGS_BEFORE["hooks"]["Stop"])
        self.assertEqual(data["hooks"]["PreToolUse"][:2], SETTINGS_BEFORE["hooks"]["PreToolUse"])
        self.assertEqual(len(data["hooks"]["PreToolUse"]), 3)
        entry = data["hooks"]["PreToolUse"][2]
        self.assertEqual(entry["matcher"], "Bash")
        self.assertEqual(entry["hooks"][0], {"type": "command", "command": "\"$CLAUDE_PROJECT_DIR\"/.claude/hooks/guard-git.py",
                                             "timeout": 15, "onFailure": "block", "statusMessage": "main himoyasi"})
        self.assertEqual(data["hooks"]["SessionStart"], [{"hooks": [{"type": "command", "timeout": 30,
                         "command": "\"$CLAUDE_PROJECT_DIR\"/.claude/quality/main_guard.py --fetch --warn-only"}]}])
        # qayta o'rnatish: hech narsa o'zgarmaydi
        after_first = snapshot(proj)
        r = self.install(proj, "--skip-tests")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(snapshot(proj), after_first)
        self.assertEqual(len(self.commands(proj, "PreToolUse")), 3)
        self.assertEqual(len(self.commands(proj, "SessionStart")), 1)
        # o'rnatilgan main_guard mustaqil ishlaydi
        r = subprocess.run([sys.executable, str(proj / ".claude/quality/main_guard.py"), "--verbose"], cwd=proj, capture_output=True, text=True, env=ENV)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_installed_guard_detects_direct_push_to_origin_main(self):
        proj, sha = self.make_project()
        self.assertEqual(self.install(proj, "--skip-tests").returncode, 0)
        other = self.tmp / "other"
        git(self.tmp, "clone", "-q", str(self.tmp / "origin.git"), str(other))
        bad = commit(other, "direct.txt")
        git(other, "push", "-q", "origin", "main")
        git(proj, "fetch", "-q", "origin")
        r = subprocess.run([sys.executable, str(proj / ".claude/quality/main_guard.py")], cwd=proj, capture_output=True, text=True, env=ENV)
        self.assertEqual(r.returncode, 1)
        self.assertIn(bad[:7], r.stderr)

    def test_differing_file_needs_force_and_nothing_is_written(self):
        proj, _ = self.make_project()
        self.assertEqual(self.install(proj, "--skip-tests").returncode, 0)
        hook = proj / ".claude" / "hooks" / "guard-git.py"
        original = hook.read_text()
        hook.write_text(original + "# mahalliy o'zgarish\n")
        before = snapshot(proj)
        r = self.install(proj, "--skip-tests")
        self.assertEqual(r.returncode, 1)
        self.assertIn("mahalliy o'zgarish", r.stderr)
        self.assertIn("--force", r.stderr)
        self.assertEqual(snapshot(proj), before)
        r = self.install(proj, "--skip-tests", "--force")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(hook.read_text(), original)

    def test_custom_protected_list_and_ref(self):
        proj, sha = self.make_project()
        r = self.install(proj, "--skip-tests", "--protected", "develop,main", "--ref", "origin/main")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(json.loads((proj / ".claude/quality/config.json").read_text()),
                         {"ref": "origin/main", "baseline": sha, "protected": ["develop", "main"], "merge_style": "merge"})
        self.assertEqual(git(proj, "config", "quality.protected").stdout.strip(), "develop,main")
        for cmd, code in (("git push origin develop", 2), ("git push origin HEAD:main", 2), ("git push origin topic", 0), ("git push origin master", 0)):
            hook = subprocess.run([sys.executable, str(proj / ".claude/hooks/guard-git.py")], capture_output=True, text=True, env=ENV,
                                  input=json.dumps({"tool_input": {"command": cmd}, "cwd": str(proj)}))
            self.assertEqual(hook.returncode, code, cmd + hook.stderr)
        # config.json saqlanadi: bayroqsiz qayta o'rnatish uni o'zgartirmaydi, boshqacha bayroq --force talab qiladi
        self.assertEqual(self.install(proj, "--skip-tests").returncode, 0)
        self.assertEqual(json.loads((proj / ".claude/quality/config.json").read_text())["protected"], ["develop", "main"])
        r = self.install(proj, "--skip-tests", "--protected", "main")
        self.assertEqual(r.returncode, 1)
        self.assertIn("config.json", r.stderr)

    def guard(self, proj):
        return subprocess.run([sys.executable, str(proj / ".claude/quality/main_guard.py")], cwd=proj, capture_output=True, text=True, env=ENV)

    def push_from_other_clone(self, message, env_extra=None):
        other = self.tmp / "other"
        if not other.exists():
            git(self.tmp, "clone", "-q", str(self.tmp / "origin.git"), str(other))
        commit(other, f"{abs(hash(message))}.txt", message, env_extra=env_extra)
        git(other, "push", "-q", "origin", "main")

    def test_merge_style_squash_is_written_kept_and_enforced(self):
        proj, _ = self.make_project()
        r = self.install(proj, "--skip-tests", "--merge-style", "squash")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("merge_style: squash", r.stdout)
        self.assertEqual(json.loads((proj / ".claude/quality/config.json").read_text())["merge_style"], "squash")
        # qayta o'rnatish (bayroqsiz va bir xil bayroq bilan) o'zgartirmaydi; boshqacha qiymat --force talab qiladi
        raw = (proj / ".claude/quality/config.json").read_bytes()
        for extra in ((), ("--merge-style", "squash")):
            self.assertEqual(self.install(proj, "--skip-tests", *extra).returncode, 0)
            self.assertEqual((proj / ".claude/quality/config.json").read_bytes(), raw)
        r = self.install(proj, "--skip-tests", "--merge-style", "any")
        self.assertEqual(r.returncode, 1)
        self.assertIn("config.json", r.stderr)
        self.assertEqual((proj / ".claude/quality/config.json").read_bytes(), raw)
        self.assertEqual(self.install(proj, "--skip-tests", "--merge-style", "any", "--force").returncode, 0)
        self.assertEqual(json.loads((proj / ".claude/quality/config.json").read_text())["merge_style"], "any")
        # o'rnatilgan main_guard: GitHub squash commit i o'tadi, foydalanuvchi committer i bilan "(#N)" o'tmaydi
        self.install(proj, "--skip-tests", "--merge-style", "squash", "--force")
        self.push_from_other_clone("Funksiya (#7)", GITHUB)
        git(proj, "fetch", "-q", "origin")
        self.assertEqual(self.guard(proj).returncode, 0)
        self.push_from_other_clone("Qo'lda (#8)")
        git(proj, "fetch", "-q", "origin")
        r = self.guard(proj)
        self.assertEqual(r.returncode, 1)
        self.assertIn("Qo'lda (#8)", r.stderr)

    def test_config_without_merge_style_stays_untouched_without_flag(self):
        proj, sha = self.make_project()
        cfg = proj / ".claude/quality/config.json"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(json.dumps({"ref": "origin/main", "baseline": sha, "protected": ["main", "master"]}, indent=2) + "\n")
        raw = cfg.read_bytes()
        self.assertEqual(self.install(proj, "--skip-tests").returncode, 0)
        self.assertEqual(cfg.read_bytes(), raw)  # kalit yo'q = merge
        self.assertEqual(self.install(proj, "--skip-tests", "--merge-style", "merge").returncode, 0)
        self.assertEqual(cfg.read_bytes(), raw)

    def test_invalid_merge_style_is_rejected_by_cli(self):
        proj, _ = self.make_project()
        before = snapshot(proj)
        r = self.install(proj, "--merge-style", "rebase")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(snapshot(proj), before)

    def test_explicit_baseline_is_used(self):
        proj, _ = self.make_project()
        base = git(proj, "rev-parse", "HEAD~1").stdout.strip()  # birinchi PR merge: undan keyin faqat ikkinchisi
        r = self.install(proj, "--skip-tests", "--baseline", base[:10])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(json.loads((proj / ".claude/quality/config.json").read_text())["baseline"], base)

    def test_missing_ref_without_baseline_fails_and_baseline_flag_fixes_it(self):
        proj, _ = self.make_project(with_origin=False)
        before = snapshot(proj)
        r = self.install(proj, "--skip-tests")
        self.assertEqual(r.returncode, 2)
        self.assertIn("--baseline", r.stderr)
        self.assertEqual(snapshot(proj), before)
        head = git(proj, "rev-parse", "HEAD").stdout.strip()
        r = self.install(proj, "--skip-tests", "--baseline", head)
        self.assertEqual(r.returncode, 1)  # origin/main yo'q: main_guard tarixni tekshira olmaydi, lekin fayllar o'rnatilgan
        self.assertIn("topilmadi", r.stderr)
        self.assertTrue((proj / ".claude/quality/config.json").is_file())

    def test_existing_entries_are_recognised_not_duplicated(self):
        settings = {"hooks": {
            "PreToolUse": [{"matcher": "Bash", "hooks": [UNRELATED, {"type": "command", "command": "\"$CLAUDE_PROJECT_DIR\"/.claude/hooks/guard-git.py", "timeout": 15}]}],
            "SessionStart": [{"hooks": [{"type": "command", "command": "\"$CLAUDE_PROJECT_DIR\"/tools/main_guard.py --fetch --warn-only"}]}],
        }}
        proj, _ = self.make_project(settings=settings)
        raw = (proj / ".claude" / "settings.json").read_bytes()
        self.assertEqual(self.install(proj, "--skip-tests").returncode, 0)
        self.assertEqual((proj / ".claude" / "settings.json").read_bytes(), raw)

    def test_invalid_settings_json_fails_without_writing(self):
        proj, _ = self.make_project()
        (proj / ".claude" / "settings.json").write_text("{broken")
        before = snapshot(proj)
        r = self.install(proj, "--skip-tests")
        self.assertEqual(r.returncode, 2)
        self.assertIn("settings.json", r.stderr)
        self.assertEqual(snapshot(proj), before)

    def test_creates_settings_json_when_absent(self):
        proj, _ = self.make_project(settings=None)
        self.assertEqual(self.install(proj, "--skip-tests").returncode, 0)
        self.assertEqual(len(self.commands(proj, "PreToolUse")), 1)
        self.assertEqual(len(self.commands(proj, "SessionStart")), 1)

    def test_rejects_non_repo_root(self):
        proj, _ = self.make_project()
        r = self.install(proj / "docs")
        self.assertEqual(r.returncode, 2)
        self.assertIn("git repo ildizi emas", r.stderr)

    def test_merge_settings_is_idempotent_and_additive(self):
        module = load_install()
        data = json.loads(json.dumps(SETTINGS_BEFORE))
        self.assertEqual(module.merge_settings(data), ["PreToolUse", "SessionStart"])
        once = json.dumps(data, sort_keys=True)
        self.assertEqual(module.merge_settings(data), [])
        self.assertEqual(json.dumps(data, sort_keys=True), once)
        self.assertEqual(data["hooks"]["PreToolUse"][:2], SETTINGS_BEFORE["hooks"]["PreToolUse"])
        self.assertEqual(module.merge_settings({}), ["PreToolUse", "SessionStart"])


if __name__ == "__main__":
    unittest.main()
