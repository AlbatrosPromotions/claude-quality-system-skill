#!/usr/bin/env python3
"""install.py testlari (faqat to'plam papkasida; loyihaga ko'chirilmaydi).

Har test o'z vaqtinchalik loyihasini quradi: bare origin, undan klon, ichida haqiqiy git-guard o'rnatuvchisi (--skip-tests) yurgizilgan va boshqa hook
yozuvlari bor oldindan mavjud .claude/settings.json. Loyiha bir marta quriladi va har testga nusxalanadi. Bitta test o'rnatilgan testlarni haqiqatan ishga
tushiradi (~5 daqiqa): o'rnatilgan joylashuv ishlashini isbotlaydi. Soxta gh PATH ning boshida, haqiqiy GitHub ga va ~/.claude ga tegilmaydi.

  python3 -m unittest discover -s tests -p 'test_install.py' -v      (to'plam papkasida)
"""
import argparse
import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
INSTALL = KIT / "install.py"
GIT_GUARD_INSTALL = KIT.parent / "git-guard" / "install.py"

HOME = tempfile.mkdtemp(prefix="rg-install-home-")
ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_") and k not in ("CLAUDE_PROJECT_DIR", "QUALITY_CONFIG")}
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
GIT_GUARD_KEYS = ("ref", "baseline", "protected", "merge_style")
DEFAULT_CONFIG = {  # shartnoma: config.json ning yangi kalitlari va standartlari (design.md "Config")
    "prefix": "quality",
    "gate_command": "make ci",
    "reviewer": "opus-reviewer",
    "source_labels": ["Source"],
    "sensitive": ["^\\.claude/", "^\\.githooks/", "^\\.github/workflows/"],
    "submodules": [],
    "spec_paths": [],
    "spec_prefix": "spec:",
    "spec_extra": [],
    "quality_log": {
        "file": "docs/quality-log.md",
        "system_prefixes": ["ci", "docs", "test", "review", "quality", "agent", "style", "arch", "git", "build", "spec", "dev", "config", "dup", "seed"],
        "minor_rule_from": None,
        "gate_script": None,
    },
}
SCRIPTS = {  # kit fayli -> loyihadagi yo'l
    "review_stamp.py": ".claude/quality/review_stamp.py",
    "gate_stamp.py": ".claude/quality/gate_stamp.py",
    "quality_log.py": ".claude/quality/quality_log.py",
    "spec_guard.py": ".claude/quality/spec_guard.py",
    "merge_guard.py": ".claude/hooks/merge-guard.py",
    "review_rounds.py": ".claude/hooks/review-rounds.py",
}
FAKE_GH = """#!/bin/sh
case "$1 $2" in
  "pr view") cat "$FAKE_GH_PR" ;;
  *) echo "fake gh: kutilmagan chaqiruv $*" >&2; exit 1 ;;
esac
"""
BASE = {}


def setUpModule():
    """Loyiha shabloni: origin ga push qilingan bitta commit, klon, ichida git-guard o'rnatilgan (testlar shundan nusxa oladi)."""
    base = Path(tempfile.mkdtemp(prefix="rg-install-base-")).resolve()
    BASE["dir"] = base
    unittest.addModuleCleanup(shutil.rmtree, base, True)  # setUpModule yiqilsa ham tozalanadi
    seed, origin, proj = base / "seed", base / "origin.git", base / "proj"
    git(base, "init", "-q", "-b", "main", str(seed))
    (seed / ".claude").mkdir()
    (seed / ".claude" / "settings.json").write_text(json.dumps(SETTINGS_BEFORE, indent=2) + "\n")
    commit(seed, "a.txt")
    git(base, "init", "-q", "--bare", "-b", "main", str(origin))
    git(seed, "push", "-q", str(origin), "main")
    git(base, "clone", "-q", str(origin), str(proj))
    r = subprocess.run([sys.executable, str(GIT_GUARD_INSTALL), str(proj), "--skip-tests"], capture_output=True, text=True, env=ENV, timeout=300)
    if r.returncode != 0:
        raise AssertionError(f"git-guard o'rnatilmadi: {r.stdout}{r.stderr}")


def tearDownModule():
    shutil.rmtree(HOME, ignore_errors=True)


def git(cwd, *args, check=True):
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, env=ENV)
    if check and r.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} ({cwd}): {r.stderr}")
    return r


def commit(repo, name):
    (Path(repo) / name).parent.mkdir(parents=True, exist_ok=True)
    (Path(repo) / name).write_text(name)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", name)
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def snapshot(root):
    """Ishchi daraxt (.git va __pycache__ siz) fayllari va ularning baytlari."""
    out = {}
    for path in sorted(Path(root).rglob("*")):
        rel = path.relative_to(root)
        if rel.parts[:1] == (".git",) or "__pycache__" in rel.parts or not path.is_file():
            continue
        out[str(rel)] = path.read_bytes()
    return out


def load_install():
    spec = importlib.util.spec_from_file_location("kit_install", INSTALL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_script(name):
    spec = importlib.util.spec_from_file_location("kit_" + name, KIT / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def kit_test_names():
    return sorted(p.name for p in (KIT / "tests").glob("test_*.py") if p.name != "test_install.py")


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rg-install-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        shutil.copytree(BASE["dir"] / "origin.git", self.tmp / "origin.git")
        shutil.copytree(BASE["dir"] / "proj", self.tmp / "proj")
        self.proj = self.tmp / "proj"
        git(self.proj, "remote", "set-url", "origin", str(self.tmp / "origin.git"))

    def install(self, *args, proj=None, timeout=300):
        return subprocess.run([sys.executable, str(INSTALL), str(proj or self.proj), *args], capture_output=True, text=True, env=ENV, timeout=timeout)

    def ok(self, *args, **kw):
        r = self.install(*args, **kw)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    def config(self):
        return json.loads((self.proj / ".claude/quality/config.json").read_text())

    def write_config(self, data):
        (self.proj / ".claude/quality/config.json").write_text(json.dumps(data, indent=2) + "\n")

    def settings(self):
        return json.loads((self.proj / ".claude/settings.json").read_text())

    def commands(self, event):
        return [h["command"] for g in self.settings()["hooks"].get(event, []) for h in g["hooks"]]

    def run_py(self, script, *args, env=None):
        return subprocess.run([sys.executable, str(script), *args], cwd=self.proj, capture_output=True, text=True, env=env or ENV, timeout=120)

    def hook(self, name, event, env=None):
        return subprocess.run([sys.executable, str(self.proj / ".claude/hooks" / name)], cwd=self.proj, input=json.dumps(event), capture_output=True,
                              text=True, env=env or ENV, timeout=120)

    # --- reja, yozish, qayta o'rnatish

    def test_dry_run_writes_nothing(self):
        before, cfg_before = snapshot(self.proj), git(self.proj, "config", "--local", "--list").stdout
        r = self.ok("--dry-run")
        self.assertIn("[dry-run]", r.stdout)
        self.assertIn(".claude/agents/opus-reviewer.md: yangi", r.stdout)
        self.assertIn(".claude/quality/.gitignore: yangi", r.stdout)
        self.assertIn("yozuvi qo'shiladi", r.stdout)
        self.assertNotIn("Keyingi qadamlar", r.stdout)
        self.assertEqual(snapshot(self.proj), before)
        self.assertEqual(git(self.proj, "config", "--local", "--list").stdout, cfg_before)

    def test_full_install_writes_every_file_and_marks_executables(self):
        r = self.ok("--skip-tests")
        self.assertIn("Keyingi qadamlar", r.stdout)
        for src, dst in SCRIPTS.items():
            self.assertEqual((self.proj / dst).read_bytes(), (KIT / src).read_bytes(), dst)
            self.assertTrue(os.access(self.proj / dst, os.X_OK), dst)
        names = kit_test_names()
        self.assertGreaterEqual(len(names), 6)
        for name in names:
            self.assertEqual((self.proj / ".claude/quality/tests" / name).read_bytes(), (KIT / "tests" / name).read_bytes(), name)
        installed = sorted(p.name for p in (self.proj / ".claude/quality/tests").glob("test_*.py"))
        self.assertEqual(installed, sorted(names + ["test_git_guard.py"]))  # test_install.py ko'chirilmaydi, git-guard testi joyida
        self.assertTrue((self.proj / ".claude/agents/opus-reviewer.md").is_file())
        self.assertEqual((self.proj / ".claude/quality/.gitignore").read_text(), "__pycache__/\n")  # testlar yoniga yozilgan bayt-kod daraxt xeshiga tushmasin
        self.assertFalse(os.access(self.proj / ".claude/quality/.gitignore", os.X_OK))
        self.assertTrue((self.proj / ".claude/hooks/guard-git.py").is_file())  # git-guard fayllariga tegilmagan

    def test_agent_is_rendered_from_the_template(self):
        self.ok("--skip-tests")
        text = (self.proj / ".claude/agents/opus-reviewer.md").read_text()
        template = (KIT / "reviewer.md").read_text()
        expected = template.replace("{reviewer}", "opus-reviewer").replace("{source_label}", "Source").replace("{gate_command}", "make ci")
        self.assertEqual(text, expected)
        self.assertTrue(text.startswith("---\nname: opus-reviewer\n"))
        self.assertNotIn("{", text)
        self.assertNotIn("}", text)
        self.assertIn("`Source:` line", text)
        self.assertIn("(`make ci`)", text)

    def test_config_merges_defaults_into_the_git_guard_keys(self):
        before = self.config()
        self.assertEqual(sorted(before), sorted(GIT_GUARD_KEYS))
        r = self.ok("--skip-tests")
        self.assertIn("yangi kalitlar qo'shiladi", r.stdout)
        after = self.config()
        self.assertEqual({k: after[k] for k in GIT_GUARD_KEYS}, before)
        self.assertEqual({k: v for k, v in after.items() if k not in GIT_GUARD_KEYS}, DEFAULT_CONFIG)

    def test_existing_config_keys_are_kept_without_flags(self):
        cfg = self.config()
        cfg.update({"gate_command": "just test", "sensitive": ["^secret/"], "source_labels": ["Manba"], "custom": {"a": 1}, "submodules": None})
        self.write_config(cfg)
        self.ok("--skip-tests")
        after = self.config()
        self.assertEqual(after["gate_command"], "just test")
        self.assertEqual(after["sensitive"], ["^secret/"])
        self.assertEqual(after["source_labels"], ["Manba"])
        self.assertEqual(after["custom"], {"a": 1})
        self.assertEqual(after["submodules"], [])  # null = kalit yo'q: standart
        self.assertEqual(after["prefix"], "quality")
        self.assertEqual(after["quality_log"], DEFAULT_CONFIG["quality_log"])
        agent = (self.proj / ".claude/agents/opus-reviewer.md").read_text()
        self.assertIn("`Manba:` line", agent)
        self.assertIn("(`just test`)", agent)

    def test_cli_values_are_applied(self):
        self.ok("--skip-tests", "--prefix", "demo", "--reviewer", "custom", "--gate-command", "just gate", "--source-labels", "Source, Manba",
                "--sensitive", "^api/auth/", "--sensitive", "\\.sql$", "--submodules", "docs/handbook,docs/specs", "--spec-paths", "tests/spec/,e2e/spec",
                "--spec-extra", "docs/matrix.md", "--spec-extra", "docs/other.md")
        after = self.config()
        expected = dict(DEFAULT_CONFIG, prefix="demo", reviewer="custom", gate_command="just gate", source_labels=["Source", "Manba"],
                        sensitive=["^api/auth/", "\\.sql$"], submodules=["docs/handbook", "docs/specs"], spec_paths=["tests/spec/", "e2e/spec"],
                        spec_extra=["docs/matrix.md", "docs/other.md"])
        self.assertEqual({k: v for k, v in after.items() if k not in GIT_GUARD_KEYS}, expected)
        agent = (self.proj / ".claude/agents/custom.md").read_text()
        self.assertTrue(agent.startswith("---\nname: custom\n"))
        self.assertIn("`Source:` line", agent)  # birinchi yorliq
        self.assertIn("(`just gate`)", agent)
        self.assertFalse((self.proj / ".claude/agents/opus-reviewer.md").exists())

    def test_cli_value_differing_from_existing_needs_force(self):
        self.ok("--skip-tests", "--gate-command", "just ci")
        before, raw = snapshot(self.proj), (self.proj / ".claude/quality/config.json").read_bytes()
        r = self.install("--skip-tests", "--gate-command", "just other")
        self.assertEqual(r.returncode, 1)
        self.assertIn("config.json", r.stderr)
        self.assertIn("just other", r.stderr)
        self.assertIn("--force", r.stderr)
        self.assertEqual(snapshot(self.proj), before)
        self.ok("--skip-tests", "--gate-command", "just ci")  # bir xil qiymat: o'zgarish yo'q
        self.ok("--skip-tests")  # bayroqsiz: mavjud qiymat saqlanadi
        self.assertEqual((self.proj / ".claude/quality/config.json").read_bytes(), raw)
        self.ok("--skip-tests", "--gate-command", "just other", "--force")
        self.assertEqual(self.config()["gate_command"], "just other")
        self.assertIn("(`just other`)", (self.proj / ".claude/agents/opus-reviewer.md").read_text())

    def test_rerun_is_idempotent(self):
        self.ok("--skip-tests", "--prefix", "demo")
        after_first = snapshot(self.proj)
        r = self.ok("--skip-tests", "--prefix", "demo")
        self.assertEqual(snapshot(self.proj), after_first)
        self.assertNotRegex(r.stdout, r"(?m): (yangi|ustiga yoziladi|yangi kalitlar)")
        self.assertEqual(len(re.findall(r"(?m): o'zgarmagan$", r.stdout)), 6 + len(kit_test_names()) + 3)  # skriptlar, testlar, .gitignore, agent, config
        self.assertIn("o'zgarmagan (yozuvlar bor)", r.stdout)
        r = self.ok("--skip-tests")  # bayroqsiz qayta o'rnatish ham o'zgartirmaydi
        self.assertEqual(snapshot(self.proj), after_first)
        for event, n in (("PreToolUse", 5), ("SessionStart", 1)):  # 2 begona + guard-git + merge-guard + review-rounds
            self.assertEqual(len(self.commands(event)), n, event)

    def test_settings_get_both_hook_groups_once_and_unrelated_entries_stay(self):
        self.ok("--skip-tests")
        data = self.settings()
        self.assertEqual(data["permissions"], SETTINGS_BEFORE["permissions"])
        self.assertEqual(data["hooks"]["Stop"], SETTINGS_BEFORE["hooks"]["Stop"])
        pre = data["hooks"]["PreToolUse"]
        self.assertEqual(pre[:2], SETTINGS_BEFORE["hooks"]["PreToolUse"])
        self.assertIn("guard-git.py", pre[2]["hooks"][0]["command"])  # git-guard yozuvi saqlangan
        self.assertEqual(pre[3:], [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "\"$CLAUDE_PROJECT_DIR\"/.claude/hooks/merge-guard.py", "timeout": 30,
                                           "onFailure": "block", "statusMessage": "merge darvozasi"}]},
            {"matcher": "Agent", "hooks": [{"type": "command", "command": "\"$CLAUDE_PROJECT_DIR\"/.claude/hooks/review-rounds.py", "timeout": 30,
                                            "onFailure": "block", "statusMessage": "ko'rik turlari chegarasi"}]},
        ])
        self.assertEqual(len([c for c in self.commands("PreToolUse") if "merge-guard.py" in c]), 1)
        self.assertEqual(len([c for c in self.commands("PreToolUse") if "review-rounds.py" in c]), 1)

    def test_existing_hook_entries_are_recognised_not_duplicated(self):
        data = self.settings()
        data["hooks"]["PreToolUse"] += [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "\"$CLAUDE_PROJECT_DIR\"/.claude/hooks/merge-guard.py", "timeout": 5}]},
            {"matcher": "Agent", "hooks": [UNRELATED, {"type": "command", "command": "python3 .claude/hooks/review-rounds.py"}]},
        ]
        (self.proj / ".claude/settings.json").write_text(json.dumps(data, indent=2) + "\n")
        raw = (self.proj / ".claude/settings.json").read_bytes()
        r = self.ok("--skip-tests")
        self.assertEqual((self.proj / ".claude/settings.json").read_bytes(), raw)
        self.assertIn("o'zgarmagan (yozuvlar bor)", r.stdout)

    def test_requires_git_guard_and_writes_nothing_without_it(self):
        bare = self.tmp / "plain"
        git(self.tmp, "init", "-q", "-b", "main", str(bare))
        commit(bare, "a.txt")
        before = snapshot(bare)
        r = self.install("--skip-tests", proj=bare)
        self.assertEqual(r.returncode, 2)
        self.assertIn("git-guard o'rnatilmagan", r.stderr)
        self.assertIn(str(GIT_GUARD_INSTALL), r.stderr)  # git-guard o'rnatish buyrug'i ko'rsatiladi
        self.assertEqual(snapshot(bare), before)
        # config.json bor, hook yo'q: baribir o'rnatilmagan
        (bare / ".claude/quality").mkdir(parents=True)
        (bare / ".claude/quality/config.json").write_text("{}\n")
        before = snapshot(bare)
        r = self.install("--skip-tests", proj=bare)
        self.assertEqual(r.returncode, 2)
        self.assertIn(".claude/hooks/guard-git.py", r.stderr)
        self.assertEqual(snapshot(bare), before)

    def test_differing_file_needs_force_and_the_diff_is_shown(self):
        self.ok("--skip-tests")
        hook = self.proj / ".claude/hooks/merge-guard.py"
        original = hook.read_text()
        hook.write_text(original + "# mahalliy o'zgarish\n")
        before = snapshot(self.proj)
        r = self.install("--skip-tests")
        self.assertEqual(r.returncode, 1)
        self.assertIn("mahalliy o'zgarish", r.stderr)
        self.assertIn(".claude/hooks/merge-guard.py", r.stderr)
        self.assertIn("--force", r.stderr)
        self.assertEqual(snapshot(self.proj), before)
        self.ok("--skip-tests", "--force")
        self.assertEqual(hook.read_text(), original)

    def test_differing_agent_file_needs_force(self):
        self.ok("--skip-tests")
        agent = self.proj / ".claude/agents/opus-reviewer.md"
        original = agent.read_text()
        agent.write_text(original + "\nMahalliy qoida.\n")
        before = snapshot(self.proj)
        r = self.install("--skip-tests")
        self.assertEqual(r.returncode, 1)
        self.assertIn("Mahalliy qoida.", r.stderr)
        self.assertEqual(snapshot(self.proj), before)
        self.ok("--skip-tests", "--force")
        self.assertEqual(agent.read_text(), original)

    def test_user_edited_quality_gitignore_is_merged_and_never_a_conflict(self):
        """Foydalanuvchi o'z qatorlarini yozgan .gitignore ziddiyat emas: `__pycache__/` yo'q bo'lsa oxiriga bir marta qo'shiladi, qatorlar saqlanadi, --force ham o'chirmaydi."""
        self.ok("--skip-tests")
        ignore = self.proj / ".claude/quality/.gitignore"
        ignore.write_text("*.local\n")
        r = self.ok("--dry-run")
        self.assertIn(".claude/quality/.gitignore: qo'shiladi", r.stdout)
        self.assertEqual(ignore.read_text(), "*.local\n")  # dry-run yozmaydi
        r = self.ok("--skip-tests")  # --force siz ham o'tadi: ziddiyat yo'q
        self.assertIn(".claude/quality/.gitignore: qo'shiladi", r.stdout)
        self.assertEqual(ignore.read_text(), "*.local\n__pycache__/\n")
        r = self.ok("--skip-tests")  # qayta o'rnatish: qator bir marta
        self.assertIn(".claude/quality/.gitignore: o'zgarmagan", r.stdout)
        self.assertEqual(ignore.read_text(), "*.local\n__pycache__/\n")
        self.ok("--skip-tests", "--force")
        self.assertEqual(ignore.read_text(), "*.local\n__pycache__/\n")

    def test_quality_gitignore_gets_the_line_after_a_missing_newline_and_lookalikes(self):
        self.ok("--skip-tests")
        ignore = self.proj / ".claude/quality/.gitignore"
        for text, expected in (("*.local", "*.local\n__pycache__/\n"), ("", "__pycache__/\n"), ("# __pycache__/\n", "# __pycache__/\n__pycache__/\n"),
                               ("/__pycache__/\n", "/__pycache__/\n__pycache__/\n"), ("__pycache__/x\n", "__pycache__/x\n__pycache__/\n"),
                               ("*.local\r\n", "*.local\r\n__pycache__/\n")):
            with self.subTest(text=text):
                ignore.write_bytes(text.encode())
                self.ok("--skip-tests")
                self.assertEqual(ignore.read_bytes(), expected.encode())

    def test_quality_gitignore_that_already_ignores_pycache_is_left_untouched(self):
        self.ok("--skip-tests")
        ignore = self.proj / ".claude/quality/.gitignore"
        for text in ("# mening\n*.local\n__pycache__/\n!keep.local\n", "__pycache__/", "  __pycache__/  \r\n*.log\r\n", "__pycache__\n", "**/__pycache__/\n", "\n\n__pycache__/\nfoo"):
            with self.subTest(text=text):
                ignore.write_bytes(text.encode())
                os.utime(ignore, (1_000_000_000, 1_000_000_000))
                r = self.ok("--skip-tests", "--force")
                self.assertIn(".claude/quality/.gitignore: o'zgarmagan", r.stdout)
                self.assertEqual(ignore.read_bytes(), text.encode())
                self.assertEqual(int(os.stat(ignore).st_mtime), 1_000_000_000)

    def test_identical_quality_gitignore_is_not_rewritten(self):
        self.ok("--skip-tests")
        ignore = self.proj / ".claude/quality/.gitignore"
        os.utime(ignore, (1_000_000_000, 1_000_000_000))
        r = self.ok("--skip-tests")
        self.assertIn(".claude/quality/.gitignore: o'zgarmagan", r.stdout)
        self.assertEqual(int(os.stat(ignore).st_mtime), 1_000_000_000)

    # CPython bayt-kodni daraxt yoniga yozsin: ba'zi Python lar (masalan macOS tizim Python i) sys.pycache_prefix bilan daraxtdan tashqariga yozadi
    PROBE = "import unittest\n\n\nclass Probe(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n"
    DISCOVER = ("import sys; sys.pycache_prefix = None; import unittest; "
                "unittest.main(module=None, argv=['unittest', 'discover', '-s', '.claude/quality/tests', '-p', 'test_zz_probe.py'])")

    def test_pycache_next_to_the_installed_tests_does_not_move_the_gate_tree(self):
        """Darvozada `python3 -m unittest discover -s .claude/quality/tests` gate_stamp.py --tree va --write --tree orasida yursa CPython testlar yoniga __pycache__
        yozadi: daraxt xeshi o'zgarib, iz har safar rad etilardi. O'rnatilgan .claude/quality/.gitignore uni xeshdan chiqaradi, -B esa umuman yozdirmaydi."""
        self.ok("--skip-tests")
        quality = self.proj / ".claude/quality"
        (quality / "tests/test_zz_probe.py").write_text(self.PROBE)
        env = {k: v for k, v in ENV.items() if k not in ("PYTHONDONTWRITEBYTECODE", "PYTHONPYCACHEPREFIX")}

        def tree():
            r = self.run_py(quality / "gate_stamp.py", "--tree", env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            return r.stdout.strip()

        def discover(*flags):
            r = subprocess.run([sys.executable, *flags, "-c", self.DISCOVER], cwd=self.proj, capture_output=True, text=True, env=env, timeout=120)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

        before = tree()
        discover("-B")
        self.assertEqual(list(self.proj.rglob("__pycache__")), [])  # -B: bayt-kod yozilmaydi
        discover()
        self.assertTrue(list((quality / "tests").glob("__pycache__/*.pyc")))  # nazorat: oddiy yurishda CPython testlar yoniga yozadi
        self.assertEqual(tree(), before)
        (quality / ".gitignore").unlink()
        self.assertNotEqual(tree(), before)  # nazorat: .gitignore siz xesh o'zgarardi

    def test_next_steps_recommend_dash_b_and_the_installer_runs_its_tests_with_it(self):
        r = self.ok("--skip-tests")
        self.assertIn("python3 -B -m unittest discover -s .claude/quality/tests", r.stdout)
        self.assertNotIn("python3 -m unittest discover", r.stdout)
        readme = (KIT / "README.md").read_text()
        self.assertIn("python3 -B -m unittest discover -s .claude/quality/tests", readme)
        self.assertNotRegex(readme, r"python3 -m unittest discover -s \.claude/quality/tests")
        self.assertIn(".claude/quality/.gitignore", readme)
        module = load_install()
        calls = []
        module.run_step = lambda title, cmd, root, timeout=None: calls.append(cmd) or True
        args = argparse.Namespace(project=str(self.proj), prefix=None, reviewer=None, gate_command=None, source_labels=None, sensitive=None, submodules=None,
                                  spec_paths=None, spec_extra=None, force=False, dry_run=False, skip_tests=False)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(module.install(args), 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1:7], ["-B", "-m", "unittest", "discover", "-s", ".claude/quality/tests"])

    def test_reviewer_rename_leaves_the_old_agent_and_says_so(self):
        self.ok("--skip-tests")
        r = self.install("--skip-tests", "--reviewer", "custom")
        self.assertEqual(r.returncode, 1)  # config dagi reviewer o'zgaradi
        self.assertIn("--force", r.stderr)
        r = self.ok("--skip-tests", "--reviewer", "custom", "--force")
        self.assertIn("eski agent fayli qoldi", r.stderr)
        self.assertIn("opus-reviewer.md", r.stderr)
        self.assertTrue((self.proj / ".claude/agents/custom.md").is_file())
        self.assertTrue((self.proj / ".claude/agents/opus-reviewer.md").is_file())
        self.assertEqual(self.config()["reviewer"], "custom")

    def test_gitignored_destinations_are_reported(self):
        (self.proj / ".gitignore").write_text(".claude/agents/\n.claude/quality/tests/\n")
        r = self.ok("--skip-tests")
        self.assertIn("OGOHLANTIRISH", r.stderr)
        self.assertIn(".claude/agents/opus-reviewer.md", r.stderr)
        self.assertIn(".claude/quality/tests/test_gate_stamp.py", r.stderr)
        self.assertNotIn(".claude/hooks/merge-guard.py", r.stderr)

    # --- tekshiruvlar

    def test_invalid_values_are_rejected_without_writing(self):
        cases = [
            ("--prefix", "bad prefix"), ("--prefix", ""), ("--prefix", "-x"), ("--reviewer", "../evil"), ("--reviewer", "a b"), ("--reviewer", "a.b"),
            ("--gate-command", "   "), ("--gate-command", "a\nb"), ("--source-labels", ","), ("--source-labels", "A:B"), ("--sensitive", "("),
            ("--sensitive", ""), ("--spec-paths", "/abs/path"), ("--spec-paths", "../up"), ("--spec-paths", "a/../b"), ("--spec-extra", "../x"),
        ]
        before = snapshot(self.proj)
        for flag, value in cases:
            with self.subTest(flag=flag, value=value):
                r = self.install("--skip-tests", f"{flag}={value}" if value.startswith("-") else flag, *([] if value.startswith("-") else [value]))
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertIn("xato:", r.stderr)
                self.assertIn(flag, r.stderr)
                self.assertEqual(snapshot(self.proj), before)

    def test_invalid_existing_config_value_is_rejected(self):
        cfg = self.config()
        cfg["source_labels"] = []
        self.write_config(cfg)
        before = snapshot(self.proj)
        r = self.install("--skip-tests")
        self.assertEqual(r.returncode, 2)
        self.assertIn("source_labels", r.stderr)
        self.assertEqual(snapshot(self.proj), before)

    def test_invalid_settings_json_fails_without_writing(self):
        (self.proj / ".claude/settings.json").write_text("{broken")
        before = snapshot(self.proj)
        r = self.install("--skip-tests")
        self.assertEqual(r.returncode, 2)
        self.assertIn("settings.json", r.stderr)
        self.assertEqual(snapshot(self.proj), before)

    def test_rejects_non_repo_root(self):
        (self.proj / "docs").mkdir()
        r = self.install(proj=self.proj / "docs")
        self.assertEqual(r.returncode, 2)
        self.assertIn("git repo ildizi emas", r.stderr)

    def test_merge_settings_is_idempotent_and_additive(self):
        module = load_install()
        data = json.loads(json.dumps(SETTINGS_BEFORE))
        self.assertEqual(module.merge_settings(data), ["merge-guard.py", "review-rounds.py"])
        once = json.dumps(data, sort_keys=True)
        self.assertEqual(module.merge_settings(data), [])
        self.assertEqual(json.dumps(data, sort_keys=True), once)
        self.assertEqual(data["hooks"]["PreToolUse"][:2], SETTINGS_BEFORE["hooks"]["PreToolUse"])
        self.assertEqual(module.merge_settings({}), ["merge-guard.py", "review-rounds.py"])
        with self.assertRaises(module.Fail):
            module.merge_settings({"hooks": {"PreToolUse": "x"}})

    def test_installer_defaults_match_the_scripts_own_defaults(self):
        inst = load_install().DEFAULTS
        stamp, guard, rounds, journal = (load_script(n).DEFAULTS for n in ("review_stamp", "merge_guard", "review_rounds", "quality_log"))
        for key in ("prefix", "gate_command", "source_labels", "sensitive"):
            self.assertEqual(inst[key], stamp[key], f"review_stamp.py: {key}")
        for key in ("prefix", "gate_command", "reviewer"):
            self.assertEqual(inst[key], guard[key], f"merge_guard.py: {key}")
        for key in ("reviewer", "submodules"):
            self.assertEqual(inst[key], rounds[key], f"review_rounds.py: {key}")
        for key in ("file", "gate_script", "minor_rule_from", "system_prefixes"):
            self.assertEqual(inst["quality_log"][key], journal[key], f"quality_log.py: {key}")
        self.assertEqual(inst["prefix"], load_script("gate_stamp").DEFAULT_PREFIX)
        spec = load_script("spec_guard")
        self.assertEqual((inst["spec_prefix"], inst["gate_command"]), (spec.DEFAULT_PREFIX, spec.DEFAULT_GATE))

    def test_render_agent_substitutes_in_one_pass_and_rejects_unknown_names(self):
        module = load_install()
        values = {"reviewer": "x", "gate_command": "make {reviewer} {a}", "source_label": "S"}
        self.assertEqual(module.render_agent("a {reviewer} b {gate_command} c {source_label}", values), "a x b make {reviewer} {a} c S")
        with self.assertRaises(module.Fail):
            module.render_agent("{reviewer} {unknown}", values)

    def test_template_and_kit_files_have_no_project_specific_names(self):
        text = (KIT / "reviewer.md").read_text()
        self.assertTrue(text.startswith("---\nname: {reviewer}\n"))
        front = text.split("---\n")[1]
        for line in ("model: opus", "effort: high", "tools: Read, Grep, Glob, Bash", "maxTurns: 80"):
            self.assertIn(line + "\n", front)
        names = re.findall(r"\{[^{}]*\}", text)
        self.assertEqual(sorted(set(names)), ["{gate_command}", "{reviewer}", "{source_label}"])
        self.assertEqual(text.count("{"), len(names))  # boshqa jingalak qavs yo'q
        self.assertEqual(text.count("}"), len(names))
        for needle in ("review_stamp.py --pr <N> --check", "Exit code 4", "VERDICT: blockers=<n> major=<n> minor=<n> security=<done|n/a>",
                       "review_stamp.py --pr <N> --head <headRefOid> --report <file>", "Never write the stamp file yourself", "security=done"):
            self.assertIn(needle, text)
        forbidden = ("sos-scrum", "/Users/", "Manba", "docs/arxitektura", "holat.md", "sifat-jurnali", "Laravel", "Angular", "UZS")
        for name in ("install.py", "reviewer.md", "README.md"):
            body = (KIT / name).read_text()
            for word in forbidden:
                self.assertNotIn(word, body, f"{name}: {word}")

    # --- o'rnatilgan joylashuv ishlaydi

    def test_installed_scripts_run_with_the_installed_config(self):
        self.ok("--skip-tests", "--prefix", "demo")
        quality = self.proj / ".claude/quality"
        r = self.run_py(quality / "review_stamp.py", "--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("--pr", r.stdout)
        tree = self.run_py(quality / "gate_stamp.py", "--tree")
        self.assertEqual(tree.returncode, 0, tree.stderr)
        self.assertRegex(tree.stdout, r"^[0-9a-f]{40}\n$")
        self.assertEqual(self.run_py(quality / "gate_stamp.py", "--write", "--tree", tree.stdout.strip()).returncode, 0)
        stamp = Path(self.proj / git(self.proj, "rev-parse", "--git-path", "demo-ci-ok").stdout.strip())  # prefiks config dan
        self.assertTrue(stamp.is_file())
        self.assertEqual(stamp.read_text().split()[0], tree.stdout.strip())
        self.assertFalse(Path(self.proj / git(self.proj, "rev-parse", "--git-path", "quality-ci-ok").stdout.strip()).exists())
        (self.proj / "docs").mkdir()
        (self.proj / "docs/quality-log.md").write_text("## Findings\n\n| Date | PR | Category | Severity | Caught by | Rule |\n|---|---|---|---|---|---|\n"
                                                      "| 2026-11-04 | #1 | ui.corner-bleed | minor | user | - |\n")
        r = self.run_py(quality / "quality_log.py", "--check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("1 findings", r.stdout)
        r = self.run_py(quality / "spec_guard.py")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("spec_paths sozlanmagan", r.stdout)

    def test_installed_hooks_and_gate_stamp_agree_through_the_installed_config(self):
        self.ok("--skip-tests", "--prefix", "demo", "--reviewer", "custom", "--gate-command", "just gate")
        git(self.proj, "checkout", "-q", "-b", "feature")
        head = commit(self.proj, "feature.txt")  # hamma narsa commit: ishchi daraxt = head daraxti
        tree = git(self.proj, "rev-parse", "HEAD^{tree}").stdout.strip()
        fake = self.tmp / "fakebin"
        fake.mkdir()
        (fake / "gh").write_text(FAKE_GH)
        (fake / "gh").chmod(0o755)
        marker = f"<!-- demo-review head={head} tree={tree} blockers=0 major=0 minor=0 security=n/a round=1 -->"
        pr = {"number": 7, "state": "OPEN", "headRefOid": head, "body": "Source: x", "baseRefName": "main", "comments": [{"body": "ko'rik\n\n" + marker + "\n"}]}
        (self.tmp / "pr.json").write_text(json.dumps(pr))
        env = dict(ENV, PATH=str(fake) + os.pathsep + ENV["PATH"], FAKE_GH_PR=str(self.tmp / "pr.json"))

        # ko'rik izini faqat sozlamadagi agent yozadi (hook config dan reviewer ni o'qiydi)
        write = {"tool_name": "Bash", "tool_input": {"command": f"python3 .claude/quality/review_stamp.py --pr 7 --head {head} --report r.md"}, "cwd": str(self.proj)}
        r = self.hook("merge-guard.py", write, env)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("custom", r.stderr)
        self.assertEqual(self.hook("merge-guard.py", dict(write, agent_type="custom"), env).returncode, 0)
        self.assertEqual(self.hook("merge-guard.py", dict(write, agent_type="opus-reviewer"), env).returncode, 2)

        # merge: darvoza izi (gate_stamp.py yozadi), keyin ko'rik izi
        merge = {"tool_name": "Bash", "tool_input": {"command": "gh pr merge 7 --merge"}, "cwd": str(self.proj)}
        r = self.hook("merge-guard.py", merge, env)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("just gate", r.stderr)
        self.assertEqual(self.run_py(self.proj / ".claude/quality/gate_stamp.py", "--write", env=env).returncode, 0)
        r = self.hook("merge-guard.py", merge, env)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("demo-review-ok-7", r.stderr)
        review_stamp = Path(self.proj / git(self.proj, "rev-parse", "--git-path", "demo-review-ok-7").stdout.strip())
        review_stamp.write_text(f"{tree} 7 {head} 2026-11-04T00:00:00Z round=1 blockers=0 major=0 minor=0\n")
        self.assertEqual(self.hook("merge-guard.py", merge, env).returncode, 0)

        # ko'rik turlari: 1-tur toza, shuning uchun 2-ko'rik chaqiruvi bloklanadi (belgi prefiksi demo config dan)
        spawn = {"tool_name": "Agent", "tool_input": {"subagent_type": "custom", "prompt": "Review PR #7 in this repo", "description": "review"}, "cwd": str(self.proj)}
        r = self.hook("review-rounds.py", spawn, env)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("Ko'rik bloklandi", r.stderr)
        self.assertIn("just gate", r.stderr)
        other = dict(spawn, tool_input=dict(spawn["tool_input"], subagent_type="opus-reviewer"))
        self.assertEqual(self.hook("review-rounds.py", other, env).returncode, 0)

    def test_installed_tests_run_for_real(self):
        r = self.ok(timeout=2400)  # o'rnatuvchining o'z chegarasi 1800 s
        self.assertRegex(r.stdout, r"ok testlar \(\.claude/quality/tests\) \([0-9.]+ s\): .*Ran \d+ tests")
        self.assertIn("OK", r.stdout)
        self.assertIn("Keyingi qadamlar", r.stdout)
        self.assertEqual(list(self.proj.rglob("__pycache__")), [])  # testlar loyiha daraxtini ifloslantirmaydi


if __name__ == "__main__":
    unittest.main()
