"""install.sh tests: the script runs from a real git clone with CLAUDE_CONFIG_DIR in a temp dir (the real ~/.claude is never touched).

  QUALITY_SKILL_SRC=~/.claude/skills/quality-system python3 -m unittest discover -s tests -v      (in the repo root)

The "published" repo is built from the root files of this checkout (install.sh, CLAUDE.md.section, .gitignore) plus skills/quality-system,
taken from QUALITY_SKILL_SRC when set (the source of truth on the Mac), else from this checkout's own skills/quality-system.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKILL_SRC = Path(os.environ.get("QUALITY_SKILL_SRC") or REPO / "skills" / "quality-system")
ROOT_FILES = ("install.sh", "CLAUDE.md.section", ".gitignore")
BASH = "/bin/bash" if os.path.exists("/bin/bash") else "bash"  # the oldest bash the script has to run under (3.2 on macOS)

HOME = tempfile.mkdtemp(prefix="qs-install-home-")
BIN = os.path.join(HOME, "bin")  # a fake `claude`, so no test starts the real one
os.makedirs(BIN)
with open(os.path.join(BIN, "claude"), "w") as f:
    f.write("#!/bin/sh\necho 'fake claude 0.0'\n")
os.chmod(os.path.join(BIN, "claude"), 0o755)
ENV = {k: v for k, v in os.environ.items()
       if not k.startswith("GIT_") and k not in ("CLAUDE_CONFIG_DIR", "QUALITY_SYSTEM_UPDATE_CHECK", "QUALITY_SYSTEM_TEST_LOG")}
ENV.update(
    HOME=HOME, PATH=BIN + os.pathsep + os.environ.get("PATH", ""), PYTHONDONTWRITEBYTECODE="1",
    GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1",
    GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com",
)


def tearDownModule():
    shutil.rmtree(HOME, ignore_errors=True)


def git(cwd, *args):
    r = subprocess.run(["git", "-C", str(cwd)] + list(args), capture_output=True, encoding="utf-8", env=ENV)
    if r.returncode != 0:
        raise AssertionError("git %s (%s): %s" % (" ".join(args), cwd, r.stderr))
    return r.stdout.strip()


def tree(root):
    """{relative path: bytes} of every file under root, without caches and Finder files."""
    root = Path(root)
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*"))
            if p.is_file() and "__pycache__" not in p.parts and p.name != ".DS_Store"}


def build_origin(parent, skill_src):
    """A git repo laid out like the install repo on GitHub: the root files of this checkout and skills/quality-system from skill_src."""
    work = Path(parent) / "origin"
    work.mkdir(parents=True)
    for name in ROOT_FILES:
        shutil.copy2(str(REPO / name), str(work / name))
    shutil.copytree(str(skill_src), str(work / "skills" / "quality-system"),
                    ignore=shutil.ignore_patterns("__pycache__", "*.bak.*", ".DS_Store"))
    git(work, "init", "--quiet")
    git(work, "add", "-A")
    git(work, "commit", "--quiet", "-m", "published")
    return work


class InstallShTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (SKILL_SRC / "SKILL.md").is_file():
            raise AssertionError("%s has no SKILL.md: set QUALITY_SKILL_SRC to the skill directory" % SKILL_SRC)
        cls.tmp = tempfile.mkdtemp(prefix="qs-install-origin-")
        cls.origin = build_origin(cls.tmp, SKILL_SRC)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="qs-install-")
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.config = self.dir / "config"  # not created here: install.sh makes it
        self.skill = self.config / "skills" / "quality-system"
        self.settings = self.config / "settings.json"
        self.claude_md = self.config / "CLAUDE.md"
        self.clone = self.dir / "claude-quality-system-skill"
        git(self.dir, "clone", "--quiet", str(self.origin), str(self.clone))

    def install(self, *args, **kw):
        """Runs `bash <script> args`; script defaults to the clone's install.sh, other keywords are extra environment."""
        script = kw.pop("script", None) or self.clone / "install.sh"
        return subprocess.run([BASH, str(script)] + list(args), capture_output=True, encoding="utf-8", errors="replace",
                              stdin=subprocess.DEVNULL, cwd=str(self.dir), env=dict(ENV, CLAUDE_CONFIG_DIR=str(self.config), **kw))

    def ok(self, *args, **kw):
        r = self.install(*args, **kw)
        self.assertEqual((r.returncode, r.stderr), (0, ""), r.stdout)
        return r

    def section(self):
        return (self.clone / "CLAUDE.md.section").read_text(encoding="utf-8")

    def record(self):
        return json.loads((self.config / "quality-system-install.json").read_text(encoding="utf-8"))

    def fake_clone(self, files):
        """A clone of an install repo whose skill is tiny: SKILL.md, the real update_check.py and the given files {path: text}."""
        skill = self.dir / "fake-skill"
        (skill / "scripts").mkdir(parents=True)
        (skill / "SKILL.md").write_text("---\nname: quality-system\n---\n", encoding="utf-8")
        if (SKILL_SRC / "scripts" / "update_check.py").is_file():
            shutil.copy2(str(SKILL_SRC / "scripts" / "update_check.py"), str(skill / "scripts"))
        for rel, body in files.items():
            (skill / rel).parent.mkdir(parents=True, exist_ok=True)
            (skill / rel).write_text(body, encoding="utf-8")
        origin = build_origin(self.dir / "fake-parent", skill)
        clone = self.dir / "fake-clone"
        git(self.dir, "clone", "--quiet", str(origin), str(clone))
        return clone

    # the skill

    def test_installs_the_skill_from_a_clone_and_says_it_is_ready(self):
        r = self.ok("--skip-tests")
        self.assertEqual(tree(self.skill), tree(self.clone / "skills" / "quality-system"))
        self.assertTrue((self.skill / "SKILL.md").is_file())
        self.assertEqual([p.name for p in (self.config / "skills").iterdir()], ["quality-system"])
        self.assertFalse(list(self.skill.rglob("__pycache__")))
        ready = [line for line in r.stdout.splitlines() if line.startswith("Tayyor.")]
        self.assertEqual(len(ready), 1, r.stdout)
        self.assertIn("/quality-system", ready[0])
        self.assertIn("skills/quality-system/SKILL.md", r.stdout)  # the list of installed files
        self.assertIn("fake claude 0.0", r.stdout)

    def test_a_second_run_changes_nothing_and_makes_no_backup(self):
        self.ok("--skip-tests")
        skill, claude_md = tree(self.skill), self.claude_md.read_bytes()
        r = self.ok("--skip-tests")
        self.assertIn("o'zgarmagan, o'tkazib yuborildi", r.stdout)
        self.assertEqual(list(self.config.rglob("*.bak.*")), [])
        self.assertEqual((tree(self.skill), self.claude_md.read_bytes()), (skill, claude_md))

    def test_caches_and_finder_files_in_the_installed_copy_do_not_count_as_a_difference(self):
        self.ok("--skip-tests")
        (self.skill / "__pycache__").mkdir()
        (self.skill / "__pycache__" / "x.cpython-39.pyc").write_bytes(b"\0")
        (self.skill / ".DS_Store").write_bytes(b"\0")
        r = self.ok("--skip-tests")
        self.assertIn("o'zgarmagan, o'tkazib yuborildi", r.stdout)
        self.assertEqual(list(self.config.rglob("*.bak.*")), [])

    def test_a_differing_existing_copy_is_backed_up_outside_the_skills_dir(self):
        self.skill.mkdir(parents=True)
        (self.skill / "SKILL.md").write_text("old version\n", encoding="utf-8")
        (self.skill / "stale.txt").write_text("only in the old copy\n", encoding="utf-8")
        r = self.ok("--skip-tests")
        backups = list(self.config.glob("quality-system.bak.*"))
        self.assertEqual(len(backups), 1, r.stdout)
        self.assertEqual((backups[0] / "SKILL.md").read_text(encoding="utf-8"), "old version\n")
        self.assertTrue((backups[0] / "stale.txt").is_file())
        self.assertIn(str(backups[0]), r.stdout)
        self.assertEqual(tree(self.skill), tree(self.clone / "skills" / "quality-system"))
        # next to the skill the backup would be a second skill (its SKILL.md) for Claude Code to load
        self.assertEqual([p.name for p in (self.config / "skills").iterdir()], ["quality-system"])

    def test_records_the_install_for_the_update_check(self):
        r = self.ok("--skip-tests")
        head = git(self.clone, "rev-parse", "HEAD")
        rec = self.record()
        self.assertEqual(rec["commit"], head)
        self.assertEqual(os.path.realpath(rec["repo_dir"]), os.path.realpath(str(self.clone)))
        self.assertEqual(os.path.realpath(rec["remote"]), os.path.realpath(str(self.origin)))
        self.assertEqual(rec["flags"], "")  # --skip-tests is not repeated by the update offer
        self.assertIn("o'rnatilgan versiya: recorded %s" % head[:7], r.stdout)

    def test_install_from_a_plain_directory_works_but_turns_update_checks_off(self):
        plain = self.dir / "unpacked"
        shutil.copytree(str(self.clone), str(plain), ignore=shutil.ignore_patterns(".git"))
        r = self.ok("--skip-tests", script=plain / "install.sh")
        self.assertIn("update checks are off", r.stdout)
        self.assertFalse((self.config / "quality-system-install.json").exists())
        self.assertEqual(tree(self.skill), tree(plain / "skills" / "quality-system"))
        self.assertIn("Tayyor.", r.stdout)

    # CLAUDE.md

    def test_claude_md_is_created_from_the_section(self):
        self.ok("--skip-tests")
        self.assertEqual(self.claude_md.read_text(encoding="utf-8"), self.section())
        self.assertIn("`quality-system`", self.section())  # what a re-run looks for

    def test_claude_md_gets_the_section_once_after_two_runs_and_keeps_the_rest(self):
        self.config.mkdir()
        self.claude_md.write_text("# Mening qoidalarim\n- bir\n", encoding="utf-8")
        self.ok("--skip-tests")
        r = self.ok("--skip-tests")
        text = self.claude_md.read_text(encoding="utf-8")
        self.assertEqual(text, "# Mening qoidalarim\n- bir\n\n" + self.section())
        self.assertEqual(text.count("# Sifat tizimi"), 1)
        self.assertIn("CLAUDE.md da Sifat tizimi bo'limi allaqachon bor", r.stdout)

    def test_a_claude_md_without_a_final_newline_gets_a_blank_line_before_the_section(self):
        self.config.mkdir()
        self.claude_md.write_text("# Mening qoidalarim\n- bir", encoding="utf-8")
        self.ok("--skip-tests")
        self.assertEqual(self.claude_md.read_text(encoding="utf-8"), "# Mening qoidalarim\n- bir\n\n" + self.section())

    def test_a_claude_md_that_already_mentions_the_skill_is_not_touched(self):
        self.config.mkdir()
        mine = "# Qoidalar\n- sifat uchun `quality-system` skill'ini yuklang\n"
        self.claude_md.write_text(mine, encoding="utf-8")
        r = self.ok("--skip-tests")
        self.assertEqual(self.claude_md.read_text(encoding="utf-8"), mine)
        self.assertIn("allaqachon bor", r.stdout)

    # settings.json and --hooks

    def test_hooks_flag_adds_the_session_start_hook_once_and_keeps_other_settings(self):
        self.config.mkdir()
        other = {"hooks": [{"type": "command", "command": "mine.sh"}]}
        self.settings.write_text(json.dumps({"theme": "dark", "hooks": {"SessionStart": [other], "Stop": [other]}}), encoding="utf-8")
        self.ok("--skip-tests", "--hooks")
        shipped = json.loads((self.clone / "skills/quality-system/references/settings-hooks.json").read_text(encoding="utf-8"))
        data = json.loads(self.settings.read_text(encoding="utf-8"))
        self.assertEqual(data["hooks"]["SessionStart"], [other] + shipped["hooks"]["SessionStart"])
        command = data["hooks"]["SessionStart"][1]["hooks"][0]["command"]
        self.assertTrue(command.endswith('/skills/quality-system/scripts/update_check.py" --hook'), command)
        self.assertEqual((data["theme"], data["hooks"]["Stop"]), ("dark", [other]))
        after_first = self.settings.read_bytes()
        r = self.ok("--skip-tests", "--hooks")
        self.assertEqual(self.settings.read_bytes(), after_first)
        self.assertIn("added 0 hook group(s), 1 already present", r.stdout)
        self.assertEqual(self.record()["flags"], "--hooks")

    def test_hooks_flag_creates_settings_json_when_there_is_none(self):
        self.assertFalse(self.settings.exists())
        self.ok("--skip-tests", "--hooks")
        data = json.loads(self.settings.read_text(encoding="utf-8"))
        self.assertEqual(list(data), ["hooks"])
        self.assertEqual(len(data["hooks"]["SessionStart"]), 1)
        self.ok("--skip-tests", "--hooks")
        self.assertEqual(len(json.loads(self.settings.read_text(encoding="utf-8"))["hooks"]["SessionStart"]), 1)

    def test_without_the_hooks_flag_settings_json_is_left_alone(self):
        r = self.ok("--skip-tests")
        self.assertFalse(self.settings.exists())
        self.assertIn("install.sh --hooks", r.stdout)  # the hint
        self.settings.write_text('{"theme":"dark"}', encoding="utf-8")
        self.ok("--skip-tests")
        self.assertEqual(self.settings.read_text(encoding="utf-8"), '{"theme":"dark"}')
        self.assertEqual(self.record()["flags"], "")

    def test_a_broken_settings_json_fails_the_run_after_the_rest_is_done(self):
        self.config.mkdir()
        self.settings.write_text("{not json", encoding="utf-8")
        r = self.install("--skip-tests", "--hooks")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("not valid JSON", r.stderr)
        self.assertIn("XATO", r.stderr)
        self.assertEqual(self.settings.read_text(encoding="utf-8"), "{not json")
        self.assertTrue((self.skill / "SKILL.md").is_file())
        self.assertTrue((self.config / "quality-system-install.json").is_file())
        self.assertNotIn("Tayyor.", r.stdout)

    # arguments and layout

    def test_unknown_parameter_is_refused_before_anything_is_written(self):
        r = self.install("--nope")
        self.assertEqual(r.returncode, 1)
        self.assertIn("noma'lum parametr: --nope", r.stderr)
        self.assertFalse(self.config.exists())

    def test_help_prints_the_usage_and_writes_nothing(self):
        r = self.install("--help")
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        for text in ("--hooks", "--skip-tests", "QUALITY_SYSTEM_UPDATE_CHECK", "review-gate"):
            self.assertIn(text, r.stdout)
        self.assertNotIn("set -euo", r.stdout)  # the header only, not the code
        self.assertFalse(self.config.exists())

    def test_a_copy_without_the_skill_is_an_error(self):
        lone = self.dir / "lone"
        lone.mkdir()
        for name in ("install.sh", "CLAUDE.md.section"):
            shutil.copy2(str(REPO / name), str(lone / name))
        r = self.install("--skip-tests", script=lone / "install.sh")
        self.assertEqual(r.returncode, 1)
        self.assertIn("XATO", r.stderr)
        self.assertIn("skills/quality-system", r.stderr)
        self.assertFalse(self.config.exists())

    # the checks

    def test_the_three_suites_run_and_a_red_one_only_warns(self):
        green = "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_ok(self):\n        pass\n"
        red = "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_red(self):\n        self.fail('boom')\n"
        clone = self.fake_clone({"scripts/tests/test_a.py": green, "scripts/review-gate/tests/test_b.py": red})
        log = self.dir / "tests.log"
        r = self.install(script=clone / "install.sh", QUALITY_SYSTEM_TEST_LOG=str(log))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)  # a red suite is a warning, the install stays
        self.assertRegex(r.stdout, r"testlar: scripts/tests: Ran 1 test in [\d.]+s OK")
        self.assertIn("testlar: scripts/git-guard/tests yo'q, o'tkazib yuborildi", r.stdout)  # no such folder: skipped with a note
        self.assertIn("OGOHLANTIRISH: scripts/review-gate/tests testlari o'tmadi, qarang: %s" % log, r.stdout)
        self.assertIn("Tayyor.", r.stdout)
        text = log.read_text(encoding="utf-8")
        self.assertIn("==> scripts/tests", text)
        self.assertIn("==> scripts/review-gate/tests", text)
        self.assertIn("boom", text)

    def test_skip_tests_runs_no_suite(self):
        red = "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_red(self):\n        self.fail('boom')\n"
        clone = self.fake_clone({"scripts/tests/test_b.py": red})
        log = self.dir / "tests.log"
        r = self.ok("--skip-tests", script=clone / "install.sh", QUALITY_SYSTEM_TEST_LOG=str(log))
        self.assertIn("testlar o'tkazib yuborildi (--skip-tests)", r.stdout)
        self.assertNotIn("OGOHLANTIRISH: scripts", r.stdout)
        self.assertFalse(log.exists())

    def test_a_script_that_does_not_compile_is_a_warning(self):
        clone = self.fake_clone({"scripts/broken.py": "def (:\n"})
        r = self.install("--skip-tests", script=clone / "install.sh")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("OGOHLANTIRISH: ba'zi skriptlar kompilyatsiya qilinmadi", r.stdout)

    # the whole point: install, a newer version appears, the session start offers it, the update takes it

    def hook(self, command, source="startup"):
        event = json.dumps({"hook_event_name": "SessionStart", "source": source, "session_id": "s"})
        p = subprocess.run(command, shell=True, input=event, capture_output=True, encoding="utf-8", env=dict(ENV, CLAUDE_CONFIG_DIR=str(self.config)))
        self.assertEqual((p.returncode, p.stderr), (0, ""))
        return json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"] if p.stdout else None

    def wait_for_check(self, condition, seconds=15):
        update = self.config / "quality-system-update.json"
        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                data = json.loads(update.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = None
            if data and condition(data):
                return data
            time.sleep(0.05)
        self.fail("the background check did not write the expected result within %d s" % seconds)

    def test_a_newer_version_is_offered_at_session_start_and_the_offered_command_installs_it(self):
        origin = build_origin(self.dir / "private", SKILL_SRC)  # this test publishes, so it has its own "GitHub"
        clone = self.dir / "private-clone"
        git(self.dir, "clone", "--quiet", str(origin), str(clone))
        self.ok("--skip-tests", "--hooks", script=clone / "install.sh")
        first = git(clone, "rev-parse", "HEAD")
        command = json.loads(self.settings.read_text(encoding="utf-8"))["hooks"]["SessionStart"][0]["hooks"][0]["command"]

        self.assertIsNone(self.hook(command))  # nothing known yet: silent, and the check starts in the background
        self.wait_for_check(lambda d: d["for"] == first and d["latest"] == first and d["error"] is None)
        update = self.config / "quality-system-update.json"
        data = json.loads(update.read_text(encoding="utf-8"))
        data["checked_at"] = "2000-01-01T00:00:00Z"  # a day later
        update.write_text(json.dumps(data), encoding="utf-8")

        with open(str(origin / "skills" / "quality-system" / "SKILL.md"), "a", encoding="utf-8") as f:
            f.write("\nnewer\n")
        git(origin, "commit", "--quiet", "-am", "second version")
        second = git(origin, "rev-parse", "HEAD")
        self.assertIsNone(self.hook(command))  # the old result says "current"; this session start only refreshes it
        self.wait_for_check(lambda d: d["latest"] == second)
        text = self.hook(command)
        where = self.record()["repo_dir"]
        self.assertIn("quality-system: a newer version of the quality-system skill is published (installed %s" % first[:7], text)
        self.assertIn("latest %s" % second[:7], text)
        self.assertIn("`git -C %s pull && bash %s/install.sh --hooks`" % (where, where), text)
        self.assertIn("second version", text)

        git(clone, "pull", "--quiet")  # what the offered command does
        self.ok("--skip-tests", "--hooks", script=clone / "install.sh")
        self.assertEqual(self.record()["commit"], second)
        self.assertEqual(len(list(self.config.glob("quality-system.bak.*"))), 1)  # the old copy, kept outside skills/
        self.assertTrue((self.skill / "SKILL.md").read_text(encoding="utf-8").endswith("\nnewer\n"))
        self.assertEqual(len(json.loads(self.settings.read_text(encoding="utf-8"))["hooks"]["SessionStart"]), 1)
        self.assertIsNone(self.hook(command))  # the stale result belongs to the old install: nothing offered, a fresh check starts
        self.wait_for_check(lambda d: d["for"] == second and d["latest"] == second)
        self.assertIsNone(self.hook(command))


if __name__ == "__main__":
    unittest.main()
