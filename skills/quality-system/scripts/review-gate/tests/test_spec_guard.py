#!/usr/bin/env python3
"""spec_guard.py testlari: spec yo'llarini faqat `spec:` commit o'zgartiradi.

Qora quti: skript subprocess orqali ishga tushiriladi, hamma git repo vaqtinchalik papkada, sozlama QUALITY_CONFIG orqali
(haqiqiy loyiha sozlamasi va git konfigiga tegilmaydi). origin/main repoda `refs/remotes/origin/main` ref i qilib qo'lda qo'yiladi.
Ikki joylashuvni biladi: to'plam papkasi va o'rnatilgan loyiha (.claude/quality).

  python3 -m unittest discover -s tests -p 'test_spec_guard.py' -v                 # to'plam papkasidan
  python3 -m unittest discover -s .claude/quality/tests -p 'test_spec_guard.py' -v  # o'rnatilgan loyihadan
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

QUALITY = Path(__file__).resolve().parent.parent  # to'plam papkasi yoki <loyiha>/.claude/quality: skript ikkala joyda ham tests/ ning ota papkasida
GUARD = QUALITY / "spec_guard.py"

HOME = tempfile.mkdtemp(prefix="spec-guard-home-")
ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_") and k not in ("QUALITY_CONFIG", "CLAUDE_PROJECT_DIR")}
ENV.update(
    HOME=HOME, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1",
    GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com",
)

SPEC_API = "api/tests/Spec/FooTest.php"
SPEC_WEB = "web/e2e/spec/foo.spec.ts"
SPEC_DOC = "docs/scenarios/foo.md"
APP = "api/app/Foo.php"
MATRIX = "docs/ac-matrix.md"
CONFIG = {"spec_paths": ["api/tests/Spec/", "web/e2e/spec/", "docs/scenarios/"], "spec_extra": [MATRIX]}

TEMPLATE = None


def setUpModule():
    # har testda `git init` + birinchi commit o'rniga tayyor repo nusxalanadi (git chaqiruvlari testlar vaqtining ko'p qismi)
    global TEMPLATE
    TEMPLATE = tempfile.mkdtemp(prefix="spec-guard-template-")
    git(TEMPLATE, "init", "-q", "-b", "main", "--template=")
    commit(TEMPLATE, "initial", {"README.md": "x", SPEC_API: "v1"})
    git(TEMPLATE, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(TEMPLATE, "checkout", "-q", "-b", "feature")


def tearDownModule():
    shutil.rmtree(HOME, ignore_errors=True)
    shutil.rmtree(TEMPLATE, ignore_errors=True)


def git(repo, *args, check=True):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=ENV)
    if check and r.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} ({repo}): {r.stderr}")
    return r.stdout.strip()


def write(repo, path, content):
    target = Path(repo) / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)


def commit(repo, subject, files=None, delete=()):
    for path, content in (files or {}).items():
        write(repo, path, content)
    if files:
        git(repo, "add", "--", *files)
    if delete:
        git(repo, "rm", "-q", "--", *delete)
    git(repo, "commit", "-q", "--allow-empty", "-m", subject)
    return git(repo, "rev-parse", "HEAD")


def new_repo(testcase):
    repo = tempfile.mkdtemp(prefix="spec-guard-repo-")
    testcase.addCleanup(shutil.rmtree, repo, True)
    shutil.rmtree(repo)
    shutil.copytree(TEMPLATE, repo)
    return repo


def advance_main(repo, subject, files=None, delete=()):
    """main ga (va origin/main ga) commit qo'shadi, feature branchga qaytadi."""
    git(repo, "checkout", "-q", "main")
    sha = commit(repo, subject, files, delete)
    git(repo, "update-ref", "refs/remotes/origin/main", sha)
    git(repo, "checkout", "-q", "feature")
    return sha


def write_config(data, name="config.json"):
    path = Path(HOME) / name
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return path


def run_guard(repo, *extra, config=CONFIG, env_extra=None, cwd=None, script=None):
    """Chiqish kodi va birlashgan chiqish. repo None = --repo berilmaydi; config: dict/satr (fayl yoziladi), Path yoki None (QUALITY_CONFIG berilmaydi)."""
    env = dict(ENV)
    if isinstance(config, (dict, str)):
        config = write_config(config)
    if config is not None:
        env["QUALITY_CONFIG"] = str(config)
    for key, value in (env_extra or {}).items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    args = [sys.executable, str(script or GUARD)] + (["--repo", str(repo)] if repo else []) + list(extra)
    r = subprocess.run(args, capture_output=True, text=True, env=env, cwd=cwd)
    return r.returncode, r.stdout + r.stderr


class SpecGuardTest(unittest.TestCase):
    def setUp(self):
        self.repo = new_repo(self)

    def assertGreen(self, *extra, **kwargs):
        code, out = run_guard(self.repo, *extra, **kwargs)
        self.assertEqual(code, 0, out)
        return out

    def assertRed(self, *needles, **kwargs):
        code, out = run_guard(self.repo, **kwargs)
        self.assertEqual(code, 1, out)
        for needle in needles:
            self.assertIn(needle, out)
        return out

    # --- oraliq ---
    def test_empty_range_is_green(self):
        out = self.assertGreen()
        self.assertIn("0 commit", out)

    def test_missing_origin_main_is_red(self):
        git(self.repo, "update-ref", "-d", "refs/remotes/origin/main")
        self.assertRed("origin/main topilmadi", "git fetch origin main")

    def test_custom_ref(self):
        git(self.repo, "update-ref", "refs/remotes/upstream/main", "main")
        commit(self.repo, "fix: x", {SPEC_API: "v2"})
        code, out = run_guard(self.repo, "--ref", "upstream/main")
        self.assertEqual(code, 1, out)

    def test_not_a_git_repo_is_red_not_a_crash(self):
        plain = tempfile.mkdtemp(prefix="spec-guard-plain-")
        self.addCleanup(shutil.rmtree, plain, True)
        code, out = run_guard(plain, env_extra={"GIT_CEILING_DIRECTORIES": os.path.realpath(os.path.dirname(plain))})
        self.assertEqual(code, 1, out)
        self.assertNotIn("Traceback", out)
        self.assertIn("QIZIL", out)

    # --- `spec:` commit ---
    def test_spec_commit_with_only_spec_files_is_green(self):
        commit(self.repo, "spec: AC-04 tests", {SPEC_API: "v2", SPEC_WEB: "w", SPEC_DOC: "d"})
        out = self.assertGreen()
        self.assertIn("1 commit", out)

    def test_spec_commit_deleting_spec_file_is_green(self):
        commit(self.repo, "spec: remove an obsolete test", delete=[SPEC_API])
        self.assertGreen()

    def test_spec_commit_with_app_file_is_red(self):
        sha = commit(self.repo, "spec: mixed", {SPEC_API: "v2", APP: "<?php"})
        out = self.assertRed(sha[:7], "spec: mixed", APP, "Tuzatish")
        self.assertNotIn(SPEC_API, out)

    def test_spec_commit_with_extra_path_is_green(self):
        commit(self.repo, "spec: AC-05", {SPEC_API: "v2", MATRIX: "# matrix"})
        self.assertGreen()

    def test_spec_commit_with_only_extra_path_is_green(self):
        commit(self.repo, "spec: matrix", {MATRIX: "# matrix"})
        self.assertGreen()

    def test_prefix_is_case_and_space_sensitive(self):
        for subject in ("Spec: capital", "SPEC: shouting", "spec : space", " spec: leading space", "fixup! spec: x", "specs: plural"):
            with self.subTest(subject=subject):
                self.repo = new_repo(self)
                sha = commit(self.repo, subject, {SPEC_API: subject})
                self.assertRed(sha[:7])

    # --- oddiy commit ---
    def test_plain_commit_elsewhere_is_green(self):
        commit(self.repo, "feat: new feature", {APP: "<?php", "web/src/app/a.ts": "x"})
        self.assertGreen()

    def test_plain_commit_touching_spec_is_red(self):
        sha = commit(self.repo, "feat: code and spec", {APP: "<?php", SPEC_API: "v2"})
        out = self.assertRed(sha[:7], "feat: code and spec", SPEC_API)
        self.assertNotIn(f"- {APP}", out)

    def test_plain_commit_touching_each_spec_dir_is_red(self):
        for path in (SPEC_API, SPEC_WEB, SPEC_DOC, "api/tests/Spec/Sub/Deep/BarTest.php"):
            with self.subTest(path=path):
                self.repo = new_repo(self)
                sha = commit(self.repo, "fix: sneaky", {path: "z"})
                self.assertRed(sha[:7], path)

    def test_plain_commit_deleting_spec_file_is_red(self):
        sha = commit(self.repo, "chore: delete a test", delete=[SPEC_API])
        self.assertRed(sha[:7], SPEC_API)

    def test_plain_commit_with_extra_path_only_is_green(self):
        commit(self.repo, "chore: matrix", {MATRIX: "# matrix"})
        self.assertGreen()

    def test_lookalike_directories_are_not_spec(self):
        commit(self.repo, "feat: lookalike paths", {"api/tests/Specification/X.php": "x", "api/tests/SpecHelper.php": "x", "docs/scenarios-old/a.md": "x"})
        self.assertGreen()

    def test_only_offending_commits_are_listed(self):
        good = commit(self.repo, "feat: good", {APP: "<?php"})
        bad = commit(self.repo, "feat: bad", {SPEC_API: "v2"})
        out = self.assertRed(bad[:7])
        self.assertNotIn(good[:7], out)

    # --- merge ---
    def test_merge_from_main_bringing_spec_file_is_green(self):
        advance_main(self.repo, "spec: another PR's scenario", {SPEC_DOC: "from main", SPEC_API: "v2"})
        commit(self.repo, "feat: work", {APP: "<?php"})
        git(self.repo, "merge", "-q", "--no-ff", "-m", "Merge origin/main", "origin/main")
        self.assertGreen()

    def test_merge_from_main_with_non_spec_commit_touching_spec_is_green(self):
        # main dagi commit lar oraliqqa kirmaydi (origin/main ning o'zi); merge faqat ularni ko'chiradi
        advance_main(self.repo, "feat: historical", {SPEC_WEB: "historical"})
        commit(self.repo, "feat: work", {APP: "<?php"})
        git(self.repo, "merge", "-q", "--no-ff", "-m", "Merge origin/main", "origin/main")
        self.assertGreen()

    def test_merge_taking_deletion_from_main_is_green(self):
        advance_main(self.repo, "spec: delete", delete=[SPEC_API])
        commit(self.repo, "feat: work", {APP: "<?php"})
        git(self.repo, "merge", "-q", "--no-ff", "-m", "Merge origin/main", "origin/main")
        self.assertGreen()

    def test_merge_of_side_branch_with_spec_commit_is_green(self):
        commit(self.repo, "feat: A", {APP: "<?php"})
        git(self.repo, "checkout", "-q", "-b", "side")
        commit(self.repo, "spec: side branch", {SPEC_DOC: "side"})
        git(self.repo, "checkout", "-q", "feature")
        commit(self.repo, "feat: B", {"web/src/app/b.ts": "b"})
        git(self.repo, "merge", "-q", "--no-ff", "-m", "Merge side", "side")
        self.assertGreen()

    def test_evil_merge_modifying_spec_file_is_red(self):
        advance_main(self.repo, "spec: another PR", {SPEC_DOC: "from main"})
        commit(self.repo, "feat: work", {APP: "<?php"})
        git(self.repo, "merge", "-q", "--no-commit", "--no-ff", "origin/main")
        write(self.repo, SPEC_DOC, "changed inside the merge")
        git(self.repo, "add", SPEC_DOC)
        git(self.repo, "commit", "-q", "-m", "Merge origin/main")
        sha = git(self.repo, "rev-parse", "HEAD")
        out = self.assertRed(sha[:7], "Merge origin/main", SPEC_DOC, "yovuz merge")
        self.assertNotIn("feat: work", out)

    def test_evil_merge_adding_new_spec_file_is_red(self):
        advance_main(self.repo, "feat: work on main", {"web/src/app/m.ts": "m"})
        commit(self.repo, "feat: work", {APP: "<?php"})
        git(self.repo, "merge", "-q", "--no-commit", "--no-ff", "origin/main")
        write(self.repo, "api/tests/Spec/Sneaky.php", "<?php")
        git(self.repo, "add", "api/tests/Spec/Sneaky.php")
        git(self.repo, "commit", "-q", "-m", "Merge origin/main")
        self.assertRed("api/tests/Spec/Sneaky.php")

    def test_evil_merge_deleting_spec_file_is_red(self):
        advance_main(self.repo, "feat: work on main", {"web/src/app/m.ts": "m"})
        commit(self.repo, "feat: work", {APP: "<?php"})
        git(self.repo, "merge", "-q", "--no-commit", "--no-ff", "origin/main")
        git(self.repo, "rm", "-q", SPEC_API)
        git(self.repo, "commit", "-q", "-m", "Merge origin/main")
        self.assertRed(SPEC_API)

    def test_evil_merge_of_side_branch_is_red(self):
        commit(self.repo, "feat: A", {APP: "<?php"})
        git(self.repo, "checkout", "-q", "-b", "side")
        commit(self.repo, "spec: side branch", {SPEC_DOC: "side"})
        git(self.repo, "checkout", "-q", "feature")
        commit(self.repo, "feat: B", {"web/src/app/b.ts": "b"})
        git(self.repo, "merge", "-q", "--no-commit", "--no-ff", "side")
        write(self.repo, SPEC_DOC, "different")
        git(self.repo, "add", SPEC_DOC)
        git(self.repo, "commit", "-q", "-m", "Merge side")
        self.assertRed(SPEC_DOC)

    # --- ishchi daraxt ---
    def test_untracked_spec_file_is_red(self):
        write(self.repo, "api/tests/Spec/README.md", "# spec")
        out = self.assertRed("api/tests/Spec/README.md", "commit qilinmagan")
        self.assertNotIn("0 commit tekshirildi, spec qoidasi buzilmagan", out)

    def test_unstaged_modification_of_spec_file_is_red(self):
        commit(self.repo, "spec: start", {SPEC_WEB: "v1"})
        write(self.repo, SPEC_WEB, "v2")
        self.assertRed(SPEC_WEB)

    def test_staged_spec_file_is_red(self):
        write(self.repo, SPEC_DOC, "new")
        git(self.repo, "add", SPEC_DOC)
        self.assertRed(SPEC_DOC)

    def test_staged_deletion_of_spec_file_is_red(self):
        git(self.repo, "rm", "-q", SPEC_API)
        self.assertRed(SPEC_API)

    def test_uncommitted_non_spec_changes_are_green(self):
        write(self.repo, APP, "<?php")
        write(self.repo, "README.md", "changed")
        write(self.repo, MATRIX, "# matrix")
        self.assertGreen()

    def test_ignored_spec_file_is_green(self):
        commit(self.repo, "chore: ignore", {".gitignore": "*.tmp\n"})
        write(self.repo, "api/tests/Spec/cache.tmp", "x")
        self.assertGreen()

    def test_clean_tree_after_spec_commit_is_green(self):
        commit(self.repo, "spec: new", {SPEC_API: "v2"})
        self.assertGreen()

    # --- hisobot ---
    def test_report_is_uzbek_and_explains_fix(self):
        commit(self.repo, "feat: bad", {SPEC_API: "v2"})
        write(self.repo, SPEC_DOC, "new")
        out = self.assertRed("QIZIL", "Qoida", "Tuzatish", "alohida `spec:", "make ci")
        self.assertIn(SPEC_DOC, out)

    def test_green_goes_to_stdout_and_red_to_stderr(self):
        r = subprocess.run([sys.executable, str(GUARD), "--repo", self.repo, "--config", str(write_config(CONFIG))], capture_output=True, text=True, env=ENV)
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        self.assertIn("spec_guard:", r.stdout)
        commit(self.repo, "feat: bad", {SPEC_API: "v2"})
        r = subprocess.run([sys.executable, str(GUARD), "--repo", self.repo, "--config", str(write_config(CONFIG))], capture_output=True, text=True, env=ENV)
        self.assertEqual((r.returncode, r.stdout), (1, ""))
        self.assertIn("spec_guard QIZIL:", r.stderr)


class SpecConfigTest(unittest.TestCase):
    def setUp(self):
        self.repo = new_repo(self)

    def test_unconfigured_spec_paths_exit_zero(self):
        for config in ({}, {"spec_paths": []}, {"spec_paths": None}, {"spec_prefix": "spec:", "spec_extra": [MATRIX]}):
            with self.subTest(config=config):
                code, out = run_guard(self.repo, config=config)
                self.assertEqual(code, 0, out)
                self.assertIn("spec_paths sozlanmagan, tekshiruv yo'q", out)

    def test_unconfigured_ignores_commits_and_missing_ref(self):
        # sozlanmagan tekshiruv git ga tegmaydi: spec yo'liga tegadigan commit ham, yo'q ref ham, git repo bo'lmagan papka ham o'tadi
        commit(self.repo, "feat: touches the default spec dir", {SPEC_API: "v2"})
        git(self.repo, "update-ref", "-d", "refs/remotes/origin/main")
        self.assertEqual(run_guard(self.repo, config={})[0], 0)
        plain = tempfile.mkdtemp(prefix="spec-guard-plain-")
        self.addCleanup(shutil.rmtree, plain, True)
        self.assertEqual(run_guard(plain, config={})[0], 0)

    def test_missing_config_file_means_unconfigured(self):
        code, out = run_guard(self.repo, config=Path(HOME) / "does-not-exist.json")
        self.assertEqual(code, 0, out)
        self.assertIn("sozlanmagan", out)

    def test_unreadable_config_warns_once_and_means_unconfigured(self):
        for text in ("{not json", "[]", ""):
            with self.subTest(text=text):
                broken = write_config(text, "broken.json")
                code, out = run_guard(self.repo, config=broken)
                self.assertEqual(code, 0, out)
                self.assertIn("sozlanmagan", out)
                notes = [line for line in out.splitlines() if line.startswith("spec_guard: ") and "broken.json" in line]
                self.assertEqual(len(notes), 1, out)
                self.assertIn("broken.json", notes[0])
                self.assertNotIn("Traceback", out)

    def test_invalid_spec_paths_is_red_not_silently_off(self):
        # noto'g'ri yozilgan spec_paths tekshiruvni jimgina o'chirib qo'ymasin
        for bad in ("api/tests/Spec/", [""], [1], ["/etc"], ["../x"], ["."], ["./"], [["a"]], {"a": 1}):
            with self.subTest(spec_paths=bad):
                code, out = run_guard(self.repo, config={"spec_paths": bad})
                self.assertEqual(code, 1, out)
                self.assertIn("spec_paths", out)
                self.assertNotIn("Traceback", out)

    def test_invalid_spec_extra_is_red(self):
        for bad in ("docs/", [""], [1], ["/etc"], ["../x"]):
            with self.subTest(spec_extra=bad):
                code, out = run_guard(self.repo, config={"spec_paths": ["api/tests/Spec/"], "spec_extra": bad})
                self.assertEqual(code, 1, out)
                self.assertIn("spec_extra", out)

    def test_spec_paths_without_trailing_slash_and_single_files(self):
        config = {"spec_paths": ["api/tests/Spec", "docs/spec.md"]}
        commit(self.repo, "feat: lookalikes", {"api/tests/SpecHelper.php": "x", "api/tests/Specification/X.php": "x", "docs/spec.md.bak": "x", "docs/spec.mdx": "x"})
        self.assertEqual(run_guard(self.repo, config=config)[0], 0)
        for path in ("api/tests/Spec/FooTest.php", "api/tests/Spec/Deep/Bar.php", "docs/spec.md"):
            with self.subTest(path=path):
                repo = new_repo(self)
                sha = commit(repo, "fix: sneaky", {path: "z"})
                code, out = run_guard(repo, config=config)
                self.assertEqual(code, 1, out)
                self.assertIn(sha[:7], out)

    def test_spec_paths_are_normalized(self):
        sha = commit(self.repo, "fix: sneaky", {SPEC_API: "v2"})
        for entry in ("./api/tests/Spec/", "api//tests/Spec", "api/tests/Spec//", "api/tests/../tests/Spec"):
            with self.subTest(entry=entry):
                code, out = run_guard(self.repo, config={"spec_paths": [entry]})
                self.assertEqual(code, 1, out)
                self.assertIn(sha[:7], out)

    def test_nonexistent_spec_paths_are_harmless(self):
        commit(self.repo, "feat: work", {APP: "<?php"})
        code, out = run_guard(self.repo, config={"spec_paths": ["no/such/dir/", "nor/this.md"]})
        self.assertEqual(code, 0, out)
        self.assertIn("1 commit", out)

    def test_custom_spec_prefix(self):
        config = {**CONFIG, "spec_prefix": "tests-first:"}
        commit(self.repo, "tests-first: AC-04", {SPEC_API: "v2"})
        self.assertEqual(run_guard(self.repo, config=config)[0], 0)
        repo = new_repo(self)
        sha = commit(repo, "spec: AC-04", {SPEC_API: "v2"})  # standart prefiks endi oddiy commit
        code, out = run_guard(repo, config=config)
        self.assertEqual(code, 1, out)
        self.assertIn(sha[:7], out)
        self.assertIn("`tests-first:", out)
        self.assertNotIn("`spec:", out)

    def test_custom_prefix_commit_may_not_touch_other_files(self):
        sha = commit(self.repo, "tests-first: mixed", {SPEC_API: "v2", APP: "<?php"})
        code, out = run_guard(self.repo, config={**CONFIG, "spec_prefix": "tests-first:"})
        self.assertEqual(code, 1, out)
        self.assertIn(sha[:7], out)
        self.assertIn(APP, out)

    def test_empty_spec_prefix_falls_back_to_default_with_a_note(self):
        # bo'sh prefiks har mavzuga mos kelib, (b) qoidasini o'chirardi
        sha = commit(self.repo, "feat: sneaky", {SPEC_API: "v2"})
        code, out = run_guard(self.repo, config={**CONFIG, "spec_prefix": ""})
        self.assertEqual(code, 1, out)
        self.assertIn(sha[:7], out)
        self.assertIn("spec_prefix", out)

    def test_spec_extra_is_empty_by_default(self):
        sha = commit(self.repo, "spec: with matrix", {SPEC_API: "v2", MATRIX: "# m"})
        code, out = run_guard(self.repo, config={"spec_paths": CONFIG["spec_paths"]})
        self.assertEqual(code, 1, out)
        self.assertIn(sha[:7], out)
        self.assertIn(MATRIX, out)

    def test_spec_extra_files_and_directories(self):
        config = {"spec_paths": CONFIG["spec_paths"], "spec_extra": ["docs/extra.md", "docs/generated/"]}
        commit(self.repo, "spec: with extras", {SPEC_API: "v2", "docs/extra.md": "e", "docs/generated/a/b.md": "g"})
        self.assertEqual(run_guard(self.repo, config=config)[0], 0)
        repo = new_repo(self)
        sha = commit(repo, "spec: with a stranger", {SPEC_API: "v2", "docs/extra.md.bak": "x", "docs/generated-old/a.md": "x"})
        code, out = run_guard(repo, config=config)
        self.assertEqual(code, 1, out)
        self.assertIn(sha[:7], out)
        self.assertIn("docs/extra.md.bak", out)
        self.assertIn("docs/generated-old/a.md", out)
        self.assertNotIn("- docs/extra.md\n", out)

    def test_report_names_configured_paths_extra_and_gate_command(self):
        commit(self.repo, "feat: bad", {SPEC_API: "v2"})
        code, out = run_guard(self.repo, config={**CONFIG, "gate_command": "just check"})
        self.assertEqual(code, 1, out)
        for expected in ("api/tests/Spec", "web/e2e/spec", "docs/scenarios", MATRIX, "`just check`"):
            self.assertIn(expected, out)
        self.assertNotIn("make ci", out)

    def test_ref_comes_from_config_and_argument_beats_it(self):
        git(self.repo, "update-ref", "refs/remotes/upstream/main", "main")
        commit(self.repo, "fix: x", {SPEC_API: "v2"})
        config = {**CONFIG, "ref": "upstream/main"}
        code, out = run_guard(self.repo, config=config)
        self.assertEqual(code, 1, out)
        self.assertIn("upstream/main bilan umumiy", out)
        git(self.repo, "update-ref", "-d", "refs/remotes/origin/main")
        code, out = run_guard(self.repo, "--ref", "origin/main", config=config)
        self.assertEqual(code, 1, out)
        self.assertIn("origin/main topilmadi", out)
        self.assertIn("git fetch origin main", out)

    def test_missing_ref_hint_follows_the_ref_name(self):
        code, out = run_guard(self.repo, "--ref", "upstream/develop")
        self.assertEqual(code, 1, out)
        self.assertIn("upstream/develop topilmadi", out)
        self.assertIn("`git fetch upstream develop`", out)

    def test_ref_without_remote_part(self):
        code, out = run_guard(self.repo, "--ref", "no-such-branch")
        self.assertEqual(code, 1, out)
        self.assertIn("no-such-branch topilmadi", out)

    def test_invalid_ref_in_config_falls_back_to_default(self):
        code, out = run_guard(self.repo, config={**CONFIG, "ref": 5})
        self.assertEqual(code, 0, out)
        self.assertIn("origin/main bilan umumiy", out)

    def use_trunk_as_the_remote_base(self):
        """Asosiy branch `trunk`: origin/trunk bor, origin/main yo'q; feature branchda spec yo'liga `spec:` siz commit (qizil bo'lishi kerak)."""
        git(self.repo, "update-ref", "refs/remotes/origin/trunk", git(self.repo, "rev-parse", "main"))
        git(self.repo, "update-ref", "-d", "refs/remotes/origin/main")
        commit(self.repo, "fix: x", {SPEC_API: "v2"})

    def test_default_ref_follows_the_first_protected_branch_like_review_stamp(self):
        self.use_trunk_as_the_remote_base()
        for protected in (["trunk"], ["trunk", "main"], ["trunk", "master"]):
            with self.subTest(protected=protected):
                code, out = run_guard(self.repo, config={**CONFIG, "protected": protected})
                self.assertEqual(code, 1, out)
                self.assertIn("origin/trunk bilan umumiy", out)
                self.assertNotIn("topilmadi", out)
        git(self.repo, "update-ref", "-d", "refs/remotes/origin/trunk")
        code, out = run_guard(self.repo, config={**CONFIG, "protected": ["trunk"]})
        self.assertEqual(code, 1, out)
        self.assertIn("origin/trunk topilmadi", out)
        self.assertIn("`git fetch origin trunk`", out)
        self.assertNotIn("origin/main", out)

    def test_config_ref_and_ref_argument_beat_protected(self):
        self.use_trunk_as_the_remote_base()
        git(self.repo, "update-ref", "refs/remotes/upstream/main", "main")
        config = {**CONFIG, "protected": ["trunk"], "ref": "upstream/main"}
        code, out = run_guard(self.repo, config=config)
        self.assertEqual(code, 1, out)
        self.assertIn("upstream/main bilan umumiy", out)
        code, out = run_guard(self.repo, "--ref", "origin/trunk", config=config)
        self.assertEqual(code, 1, out)
        self.assertIn("origin/trunk bilan umumiy", out)

    def test_invalid_protected_falls_back_to_origin_main_with_a_note(self):
        for protected in ([], "trunk", [""], [5], ["trunk", ""], {"a": 1}):
            with self.subTest(protected=protected):
                code, out = run_guard(self.repo, config={**CONFIG, "protected": protected})
                self.assertEqual(code, 0, out)
                self.assertIn("origin/main bilan umumiy", out)
                self.assertIn("config protected", out)
        code, out = run_guard(self.repo, config={**CONFIG, "protected": None})  # null = kalit yo'q: jim
        self.assertEqual(code, 0, out)
        self.assertNotIn("protected", out)

    def test_invalid_protected_is_silent_when_config_ref_or_argument_decides(self):
        """Standart ref faqat kerak bo'lganda hisoblanadi: config `ref` yoki --ref bor bo'lsa yaroqsiz `protected` haqida ogohlantirish chiqmaydi, tekshiruv berilgan ref ga qarshi."""
        git(self.repo, "update-ref", "refs/remotes/upstream/dev", "main")
        commit(self.repo, "fix: x", {SPEC_API: "v2"})
        for protected in ([], "trunk", [""], [5], ["trunk", ""], {"a": 1}):
            with self.subTest(protected=protected):
                code, out = run_guard(self.repo, config={**CONFIG, "ref": "upstream/dev", "protected": protected})
                self.assertEqual(code, 1, out)
                self.assertIn("upstream/dev bilan umumiy", out)
                for text in ("protected", "standart", "origin/main", "ogohlantirish"):
                    self.assertNotIn(text, out)
                code, out = run_guard(self.repo, "--ref", "upstream/dev", config={**CONFIG, "protected": protected})
                self.assertEqual(code, 1, out)
                self.assertIn("upstream/dev bilan umumiy", out)
                self.assertNotIn("protected", out)

    def test_invalid_ref_in_config_still_reports_both_when_the_default_is_used(self):
        code, out = run_guard(self.repo, config={**CONFIG, "ref": "", "protected": []})
        self.assertEqual(code, 0, out)
        self.assertIn("origin/main bilan umumiy", out)
        self.assertIn("config protected", out)
        self.assertIn("config ref", out)


class LayoutTest(unittest.TestCase):
    """Skript nusxasi o'rnatilgan joylashuvda: sozlama skript yonida, repo = skript papkasining git ildizi."""

    def setUp(self):
        self.repo = new_repo(self)
        self.tmp = tempfile.mkdtemp(prefix="spec-guard-layout-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.script_dir = Path(self.repo) / ".claude" / "quality"
        self.script_dir.mkdir(parents=True)
        self.script = self.script_dir / "spec_guard.py"
        shutil.copy2(GUARD, self.script)

    def test_default_repo_is_git_toplevel_of_the_script_dir_and_config_sits_next_to_it(self):
        (self.script_dir / "config.json").write_text(json.dumps(CONFIG))
        elsewhere = Path(self.tmp) / "elsewhere"
        elsewhere.mkdir()
        sha = commit(self.repo, "fix: sneaky", {SPEC_API: "v2"})
        code, out = run_guard(None, config=None, cwd=elsewhere, script=self.script)
        self.assertEqual(code, 1, out)
        self.assertIn(sha[:7], out)

    def test_default_repo_green_path(self):
        (self.script_dir / "config.json").write_text(json.dumps(CONFIG))
        commit(self.repo, "spec: good", {SPEC_API: "v2"})
        code, out = run_guard(None, config=None, cwd=self.tmp, script=self.script)
        self.assertEqual(code, 0, out)
        self.assertIn("1 commit", out)

    def test_without_any_config_the_installed_script_is_unconfigured(self):
        code, out = run_guard(None, config=None, cwd=self.tmp, script=self.script)
        self.assertEqual(code, 0, out)
        self.assertIn("sozlanmagan", out)

    def test_default_repo_falls_back_to_current_directory(self):
        plain = Path(self.tmp) / "plain"  # git repo emas
        plain.mkdir()
        shutil.copy2(GUARD, plain / "spec_guard.py")
        sha = commit(self.repo, "fix: sneaky", {SPEC_API: "v2"})
        code, out = run_guard(None, config=CONFIG, cwd=self.repo, script=plain / "spec_guard.py", env_extra={"GIT_CEILING_DIRECTORIES": os.path.realpath(self.tmp)})
        self.assertEqual(code, 1, out)
        self.assertIn(sha[:7], out)

    def test_explicit_repo_beats_the_default(self):
        other = new_repo(self)
        commit(other, "fix: sneaky", {SPEC_API: "v2"})
        code, out = run_guard(other, config=CONFIG, cwd=self.tmp, script=self.script)
        self.assertEqual(code, 1, out)

    def test_config_precedence_argument_then_env_then_script_dir(self):
        # uchta config, har biri boshqa papkani spec deb biladi; commit uchala papkaga ham tegadi
        (self.script_dir / "config.json").write_text(json.dumps({"spec_paths": ["from-script/"]}))
        env_config = write_config({"spec_paths": ["from-env/"]}, "env.json")
        arg_config = write_config({"spec_paths": ["from-arg/"]}, "arg.json")
        sha = commit(self.repo, "fix: sneaky", {"from-script/a": "1", "from-env/a": "1", "from-arg/a": "1"})

        def run_with(*extra, env_path=None):
            env = dict(ENV)
            if env_path:
                env["QUALITY_CONFIG"] = str(env_path)
            r = subprocess.run([sys.executable, str(self.script), "--repo", self.repo, *extra], capture_output=True, text=True, env=env, cwd=self.tmp)
            return r.returncode, r.stdout + r.stderr

        for label, (code, out), only in (
            ("script dir", run_with(), "from-script/a"),
            ("env", run_with(env_path=env_config), "from-env/a"),
            ("--config", run_with("--config", str(arg_config), env_path=env_config), "from-arg/a"),
        ):
            with self.subTest(source=label):
                self.assertEqual(code, 1, out)
                self.assertIn(sha[:7], out)
                self.assertIn(only, out)
                for other in ("from-script/a", "from-env/a", "from-arg/a"):
                    if other != only:
                        self.assertNotIn(other, out)


class CommandLineTest(unittest.TestCase):
    def test_script_is_executable_python3(self):
        self.assertTrue(os.access(GUARD, os.X_OK), f"{GUARD} bajariladigan emas")
        self.assertEqual(GUARD.read_text(encoding="utf-8").splitlines()[0], "#!/usr/bin/env python3")

    def test_help_describes_the_rules_and_options(self):
        code, out = run_guard(None, "--help")
        self.assertEqual(code, 0, out)
        for expected in ("--repo", "--ref", "--config", "spec_prefix", "spec_extra", "spec_paths"):
            self.assertIn(expected, out)

    def test_unknown_option_is_a_usage_error(self):
        self.assertEqual(run_guard(None, "--nonsense")[0], 2)


if __name__ == "__main__":
    unittest.main()
