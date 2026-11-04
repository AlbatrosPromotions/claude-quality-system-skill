#!/usr/bin/env python3
"""review_rounds.py hooki testlari (PreToolUse Agent: ko'rikchi agent chaqiruvi ko'rik turlari chegarasida).

Qora quti: hook stdin JSON ({tool_name, tool_input, cwd}) bilan ishga tushadi va haqiqiy review_stamp.py --check ni yuritadi;
gh o'rniga PATH ning boshidagi soxta skript (PR: <FAKE_GH_DIR>/<papka nomi>.json, yo'q bo'lsa pr.json), git repo lar vaqtinchalik papkada,
sozlama QUALITY_CONFIG orqali vaqtinchalik JSON. Ikki joylashuvni biladi: to'plam papkasi (review_rounds.py va review_stamp.py yonma-yon)
va o'rnatilgan loyiha (.claude/hooks/review-rounds.py, .claude/quality/review_stamp.py, .claude/quality/tests).

  python3 -m unittest discover -s .claude/quality/tests -p 'test_review_*.py' -v
"""
import atexit
import collections
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

QUALITY = Path(__file__).resolve().parent.parent  # to'plam papkasi yoki <loyiha>/.claude/quality
STAMP = QUALITY / "review_stamp.py"
HOOK = QUALITY / "review_rounds.py" if (QUALITY / "review_rounds.py").is_file() else QUALITY.parent / "hooks" / "review-rounds.py"

# Sozlama misoli (generik standartlardan ataylab farqli): hook shu qiymatlarni config dan o'qishi kerak
REVIEWER = "code-critic"
PREFIX = "acme"
SUB, SUB2 = "libs/shared", "libs/extra"
EXAMPLE = {"prefix": PREFIX, "reviewer": REVIEWER, "submodules": [SUB, SUB2], "gate_command": "make ci"}

HOME = tempfile.mkdtemp(prefix="rounds-hook-home-")
atexit.register(shutil.rmtree, HOME, ignore_errors=True)  # modul import qilinib testlari ishga tushmasa ham qolmasin
BASE_ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_") and k not in ("CLAUDE_PROJECT_DIR", "QUALITY_CONFIG")}
BASE_ENV.update(
    HOME=HOME, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1",
    GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com",
)
if sys.pycache_prefix:  # tizim Python ida bayt-kod keshi HOME ga bog'liq: HOME almashgach har bola jarayon stdlib ni qaytadan kompilyatsiya qilardi
    BASE_ENV["PYTHONPYCACHEPREFIX"] = sys.pycache_prefix

# Soxta gh (sh): `pr view` joriy papka nomidagi <papka>.json ni, yo'q bo'lsa pr.json ni qaytaradi (ikkalasi ham yo'q: xato); hamma chaqiruv calls.log da
FAKE_GH = """#!/bin/sh
d="$FAKE_GH_DIR"
name=$(basename "$(pwd -P)")
printf '%s: %s\\n' "$name" "$*" >> "$d/calls.log"
if [ "$1" != pr ] || [ "$2" != view ]; then exit 64; fi
for p in "$d/$name.json" "$d/pr.json"; do
  if [ -f "$p" ]; then cat "$p"; exit 0; fi
done
echo "no pull requests found" >&2
exit 1
"""

# Soxta review_stamp.py: chaqiruvini STUB_LOG ga yozadi, STUB_STDERR ni stderr ga chiqaradi, STUB_EXIT kodi bilan tugaydi
STUB = """#!/usr/bin/env python3
import json, os, sys
with open(os.environ["STUB_LOG"], "a") as f:
    f.write(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd(), "qc": os.environ.get("QUALITY_CONFIG")}) + "\\n")
sys.stderr.write(os.environ.get("STUB_STDERR", ""))
sys.exit(int(os.environ.get("STUB_EXIT", "0")))
"""

BODY = "## Xulosa\nNimadir.\n\nSource: docs/spec.md 3-bo'lim\n"
BRIEF = "PR #7 ni ko'rib chiq, repo papkasi /x"
SUB_BRIEF = f"PR #7 ni ko'rib chiq, repo papkasi {SUB}"
THIRD = "3-ko'rik faqat foydalanuvchi ruxsati bilan"
FOURTH = "4-ko'rik faqat foydalanuvchi ruxsati bilan"
DEFAULT_REVIEWER = "opus-reviewer"
DROP = object()  # event maydonini butunlay olib tashlash
USE_REVIEWER = object()  # agent parametri: sinfning sozlangan ko'rikchisi
Result = collections.namedtuple("Result", "returncode stdout stderr")

TEMPLATE = Path(HOME) / "template-repo"  # main + origin/main + feature (change.txt): setUp bunga nusxa oladi
TEMPLATE_HEAD = None


def git(cwd, *args, check=True):
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, env=BASE_ENV)
    if check and r.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} ({cwd}): {r.stderr}")
    return r.stdout.strip()


def commit(repo, name):
    p = Path(repo) / name
    p.write_text(name)
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", name)
    return git(repo, "rev-parse", "HEAD")


def make_repo(path):
    """main da bitta commit va `origin/main` ref (review_stamp.py shuni kutadi), feature da bitta commit; qaytaradi feature head."""
    path.parent.mkdir(parents=True, exist_ok=True)
    git(path.parent, "init", "-q", "-b", "main", str(path))
    commit(path, "base.txt")
    git(path, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(path, "switch", "-q", "-c", "feature")
    return commit(path, "change.txt")


def setUpModule():
    global TEMPLATE_HEAD
    TEMPLATE_HEAD = make_repo(TEMPLATE)


def tearDownModule():
    shutil.rmtree(HOME, ignore_errors=True)


def executable(path, text=None):
    if text is not None:
        Path(path).write_text(text)
    Path(path).chmod(Path(path).stat().st_mode | stat.S_IXUSR)


class HookTestCase(unittest.TestCase):
    CONFIG = EXAMPLE  # None: config fayli yo'q (standart qiymatlar)
    prefix = PREFIX
    reviewer = REVIEWER

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rounds-hook-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.gh_dir = self.tmp / "gh"
        (self.gh_dir / "bin").mkdir(parents=True)
        executable(self.gh_dir / "bin" / "gh", FAKE_GH)
        self.env = dict(BASE_ENV, FAKE_GH_DIR=str(self.gh_dir), PATH=f"{self.gh_dir / 'bin'}{os.pathsep}{os.environ['PATH']}")
        self.set_config(self.CONFIG)
        self.repo = self.tmp / "repo"
        shutil.copytree(TEMPLATE, self.repo, symlinks=True)
        self.head = TEMPLATE_HEAD
        self.set_pr()

    def set_config(self, cfg, name="config.json"):
        """QUALITY_CONFIG ni vaqtinchalik fayl ga yo'naltiradi: dict/str yoziladi, None = fayl yo'q."""
        path = self.tmp / name
        if cfg is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(cfg if isinstance(cfg, str) else json.dumps(cfg))
        self.env["QUALITY_CONFIG"] = str(path)
        return path

    def set_pr(self, comments=(), name="pr", **over):
        """PR ma'lumoti <name>.json ga; qiymati None bo'lgan kalit tashlanadi (masalan comments=None: `comments` siz javob)."""
        pr = {"number": 7, "state": "OPEN", "headRefOid": self.head, "body": BODY, "baseRefName": "main", "comments": comments}
        pr.update(over)
        (self.gh_dir / f"{name}.json").write_text(json.dumps({k: v for k, v in pr.items() if v is not None}))

    def review_comment(self, blockers=0, major=0, minor=2, round=1, prefix=None):
        """Ko'rikchi hisoboti + tur belgisi: review_stamp.py PR izohiga shunday yozadi."""
        marker = (f"<!-- {prefix or self.prefix}-review head={'a' * 40} tree={'b' * 40} blockers={blockers} major={major} minor={minor} "
                  f"security=n/a round={round} -->")
        return {"body": f"# Ko'rik\n\nHisobot matni.\n\nVERDICT: blockers={blockers} major={major} minor={minor} security=n/a\n\n{marker}\n"}

    def hook(self, prompt=BRIEF, agent=USE_REVIEWER, tool="Agent", description="Mustaqil ko'rik", cwd=None, raw=None, event=None,
             hook=None, args=(), env=None, run_cwd=None):
        if raw is None:
            payload = {"tool_name": tool, "tool_input": {"subagent_type": self.reviewer if agent is USE_REVIEWER else agent,
                                                          "prompt": prompt, "description": description}, "cwd": str(cwd or self.repo)}
            for key, value in (event or {}).items():
                if value is DROP:
                    payload.pop(key, None)
                else:
                    payload[key] = value
            raw = json.dumps(payload)
        r = subprocess.run([str(hook or HOOK), *args], input=raw if isinstance(raw, bytes) else raw.encode(), capture_output=True,
                           env=env or self.env, cwd=str(run_cwd or self.tmp))
        return Result(r.returncode, r.stdout.decode(errors="replace"), r.stderr.decode(errors="replace"))

    def calls(self):
        p = self.gh_dir / "calls.log"
        return p.read_text().splitlines() if p.exists() else []

    def allow_file(self, repo, prefix=None):
        return Path(repo) / git(repo, "rev-parse", "--git-path", f"{prefix or self.prefix}-review-allow")

    def assertPasses(self, r):
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((r.stdout, r.stderr), ("", ""))

    def assertLimited(self, r, text=None):
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertTrue(r.stderr.startswith("Ko'rik bloklandi: "), r.stderr)
        self.assertIn("Qoida: loyiha CLAUDE.md ko'rik qoidasi.", r.stderr)
        self.assertEqual(r.stdout, "")
        if text:
            self.assertIn(text, r.stderr)

    def assertCheckedIn(self, repo_name, number="7"):
        """gh ga bitta `pr view <number>` chaqiruvi, aynan shu papka nomidan."""
        calls = self.calls()
        self.assertEqual(len(calls), 1, calls)
        self.assertTrue(calls[0].startswith(f"{repo_name}: pr view {number} "), calls)

    def make_layout(self, name, *, stamp="real", config=None, kit=False):
        """<tmp>/<name> da o'rnatilgan joylashuv: hooks/review-rounds.py, quality/review_stamp.py, quality/config.json (kit=True: yonma-yon)."""
        root = self.tmp / name
        hooks, quality = (root, root) if kit else (root / "hooks", root / "quality")
        hooks.mkdir(parents=True, exist_ok=True)
        quality.mkdir(parents=True, exist_ok=True)
        shutil.copy(HOOK, hooks / ("review_rounds.py" if kit else "review-rounds.py"))
        if stamp == "real":
            shutil.copy(STAMP, quality / "review_stamp.py")
        elif stamp == "stub":
            executable(quality / "review_stamp.py", STUB)
        if config is not None:
            (quality / "config.json").write_text(json.dumps(config))
        return hooks / ("review_rounds.py" if kit else "review-rounds.py")

    def env_without_config(self, **extra):
        env = {k: v for k, v in self.env.items() if k != "QUALITY_CONFIG"}
        env.update(extra)
        return env


class PassThroughTests(HookTestCase):
    def test_hook_is_executable_python_script(self):
        self.assertTrue(os.access(HOOK, os.X_OK))
        text = HOOK.read_text()
        self.assertTrue(text.startswith("#!/usr/bin/env python3\n"))
        compile(text, str(HOOK), "exec")

    def test_other_tools_and_agents_pass_without_checking(self):
        self.set_pr([self.review_comment()])  # tekshirilsa bloklanardi
        cases = ({"tool": "Bash"}, {"tool": "Task"}, {"agent": "sonnet-coder"}, {"agent": "opus-auditor"}, {"agent": None}, {"agent": ""},
                 {"agent": DEFAULT_REVIEWER}, {"agent": REVIEWER.upper()}, {"agent": REVIEWER + "-2"}, {"agent": " " + REVIEWER}, {"agent": [REVIEWER]})
        for kwargs in cases:
            with self.subTest(**kwargs):
                self.assertPasses(self.hook(**kwargs))
        self.assertEqual(self.calls(), [])

    def test_agent_type_of_the_event_does_not_change_the_decision(self):
        """`agent_type` (subagent ichidan chaqiruvda bor) qaror bermaydi: faqat tool_input.subagent_type hal qiladi."""
        self.set_pr([self.review_comment()])
        for agent_type in (None, "", "sonnet-coder", REVIEWER, DEFAULT_REVIEWER):
            with self.subTest(agent_type=agent_type):
                self.assertLimited(self.hook(event={"agent_type": agent_type}), "1-ko'rigi toza")
                self.assertPasses(self.hook(agent="sonnet-coder", event={"agent_type": agent_type}))
        self.assertPasses(self.hook(agent="sonnet-coder", event={"agent_type": REVIEWER}))

    def test_prompt_without_pr_number_passes(self):
        self.set_pr([self.review_comment()])
        for prompt in ("Ko'rib chiq, repo papkasi /x", "", f"papka {SUB}, raqam yo'q", "PRD 7 ni ko'r", "APR 7", "pr", "PR #x", "issue #7"):
            with self.subTest(prompt=prompt):
                self.assertPasses(self.hook(prompt=prompt, description=""))
        self.assertEqual(self.calls(), [])

    def test_first_round_passes(self):
        self.assertPasses(self.hook())
        self.assertCheckedIn("repo")

    def test_comments_without_review_markers_are_not_rounds(self):
        self.set_pr([{"body": "LGTM"}, {"body": ""}, {"body": None}, {"body": f"<!-- {PREFIX}-review head=zz -->"}, "satr", 5])
        self.assertPasses(self.hook())

    def test_markers_of_another_prefix_are_not_rounds(self):
        self.set_pr([self.review_comment(prefix="other"), self.review_comment(prefix="x" + PREFIX)])
        self.assertPasses(self.hook())


class RoundLimitTests(HookTestCase):
    def test_clean_first_round_blocks_second(self):
        self.set_pr([self.review_comment(blockers=0, major=0, minor=2)])
        r = self.hook()
        self.assertLimited(r, "1-ko'rigi toza")
        self.assertCheckedIn("repo")

    def test_block_message_shape(self):
        self.set_pr([self.review_comment()])
        r = self.hook()
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout, "")
        self.assertRegex(r.stderr, r"^Ko'rik bloklandi: ko'rik turlari chegarasi: PR #7 .* Qoida: loyiha CLAUDE\.md ko'rik qoidasi\.\n$")
        self.assertEqual(len(r.stderr.splitlines()), 1)

    def test_pr_number_forms(self):
        self.set_pr([self.review_comment()])
        cases = (("PR #7 ni ko'rib chiq", "", "7"), ("PR 7 ni ko'rib chiq", "", "7"), ("pr #7, repo papkasi /x", "", "7"), ("PR#7", "", "7"),
                 ("repo papkasi /x, --pr 7", "", "7"), ("--pr 7", "", "7"), ("Ko'rik: PR #123 va keyin", "", "123"),
                 ("repo papkasi /x", "PR #7 ko'rik", "7"), ("PR #7 va PR #8", "", "7"), ("repo /x", "PR #5", "5"))
        for prompt, description, number in cases:
            with self.subTest(prompt=prompt, description=description):
                (self.gh_dir / "calls.log").unlink(missing_ok=True)
                self.assertLimited(self.hook(prompt=prompt, description=description), "1-ko'rigi toza")
                self.assertCheckedIn("repo", number)

    def test_description_is_searched_before_the_prompt(self):
        self.set_pr([self.review_comment()])
        self.assertLimited(self.hook(prompt="PR #8 haqida", description="PR #7 ko'rik"))
        self.assertCheckedIn("repo", "7")

    def test_legacy_marker_without_round_counts_as_round(self):
        legacy = {"body": f"Hisobot\n\n<!-- {PREFIX}-review head={'a' * 40} tree={'b' * 40} blockers=0 major=0 minor=1 security=done -->\n"}
        self.set_pr([legacy])
        self.assertLimited(self.hook(), "1-ko'rigi toza")

    def test_second_round_allowed_after_blockers_or_major(self):
        for kwargs in ({"blockers": 2}, {"major": 1}, {"blockers": 1, "major": 3, "minor": 4}):
            with self.subTest(**kwargs):
                self.set_pr([self.review_comment(**kwargs)])
                self.assertPasses(self.hook())

    def test_third_round_needs_user_allow(self):
        self.set_pr([self.review_comment(blockers=1, major=2, minor=3), self.review_comment(round=2)])
        r = self.hook()
        self.assertLimited(r, THIRD)
        self.assertIn("--pr 7 --allow --round 3", r.stderr)
        self.assertIn("1-tur bloker 1 muhim 2 mayda 3", r.stderr)
        self.assertFalse(self.allow_file(self.repo).exists())

    def test_allow_file_opens_third_round_and_check_keeps_it(self):
        self.set_pr([self.review_comment(blockers=1), self.review_comment(round=2)])
        allow = self.allow_file(self.repo)
        allow.write_text("7 3 t\n")
        self.assertPasses(self.hook())
        self.assertEqual(allow.read_text(), "7 3 t\n")
        self.assertTrue(all(" pr view " in c for c in self.calls()), self.calls())

    def test_allow_for_other_pr_or_round_does_not_open_it(self):
        self.set_pr([self.review_comment(blockers=1), self.review_comment(round=2)])
        allow = self.allow_file(self.repo)
        for content in ("8 3 t\n", "7 4 t\n", "7 2 t\n", "7\n", "x y z\n", ""):
            with self.subTest(content=content):
                allow.write_text(content)
                self.assertLimited(self.hook(), THIRD)

    def test_allow_file_of_another_prefix_does_not_open_it(self):
        self.set_pr([self.review_comment(blockers=1), self.review_comment(round=2)])
        self.allow_file(self.repo, "other").write_text("7 3 t\n")
        self.assertLimited(self.hook(), THIRD)

    def test_fourth_round_needs_its_own_allow(self):
        self.set_pr([self.review_comment(blockers=1), self.review_comment(round=2, blockers=1), self.review_comment(round=3, blockers=1)])
        allow = self.allow_file(self.repo)
        allow.write_text("7 3 t\n")
        self.assertLimited(self.hook(), FOURTH)
        allow.write_text("7 4 t\n")
        self.assertPasses(self.hook())


class FailOpenTests(HookTestCase):
    """Chegara (chiqish kodi 4) bo'lmagan hamma xato o'tkaziladi: iz yozishda skriptning o'zi chegarani baribir majburlaydi."""

    def test_gh_failure_passes(self):
        (self.gh_dir / "pr.json").unlink()
        self.assertPasses(self.hook())
        self.assertCheckedIn("repo")

    def test_unusable_pr_data_passes(self):
        for case in ({"state": "MERGED"}, {"state": "CLOSED"}, {"baseRefName": "develop"}, {"comments": None}, {"comments": "yo'q"}):
            with self.subTest(**case):
                self.set_pr(**{"comments": [self.review_comment()], **case})
                self.assertPasses(self.hook())
        for text in ("not json", "[]", "null", ""):
            with self.subTest(answer=text):
                (self.gh_dir / "pr.json").write_text(text)
                self.assertPasses(self.hook())

    def test_non_repo_cwd_passes(self):
        self.set_pr([self.review_comment()])
        self.assertPasses(self.hook(cwd=self.tmp))

    def test_missing_cwd_directory_passes(self):
        self.set_pr([self.review_comment()])
        self.assertPasses(self.hook(cwd=self.tmp / "no-such-dir"))

    def test_cwd_that_cannot_be_a_directory_passes(self):
        self.set_pr([self.review_comment()])
        self.assertPasses(self.hook(event={"cwd": "bad\x00dir"}))

    def test_broken_stdin_passes(self):
        self.set_pr([self.review_comment()])
        for raw in ("", "not json", "[]", "null", "5", '"x"', "{}", '{"tool_name": "Agent"}', '{"tool_name": "Agent", "tool_input": null}',
                    '{"tool_name": "Agent", "tool_input": []}', '{"tool_name": "Agent", "tool_input": "x"}', b"\xff\xfe\x00", b'{"tool_name": "\xff"}'):
            with self.subTest(raw=raw):
                self.assertPasses(self.hook(raw=raw))

    def test_unexpected_exception_passes_with_a_note(self):
        """Haddan tashqari chuqur JSON json.load da RecursionError beradi (ValueError emas): hook o'zi qulab ko'rikni to'xtatmaydi."""
        self.set_pr([self.review_comment()])
        r = self.hook(raw="[" * 100000)
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)
        self.assertIn("kutilmagan xato", r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_odd_field_types_pass(self):
        self.set_pr([self.review_comment()])
        for event in ({"cwd": 5}, {"cwd": ["x"]}, {"tool_input": {"subagent_type": REVIEWER, "prompt": ["PR #7"], "description": 5}}):
            with self.subTest(event=event):
                self.assertPasses(self.hook(event=event, cwd=self.tmp))

    def test_missing_stamp_script_passes_with_a_note(self):
        hook = self.make_layout("nostamp", stamp=None, config=EXAMPLE)
        self.set_pr([self.review_comment()])
        r = self.hook(hook=hook)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertIn("review_stamp.py topilmadi", r.stderr)
        self.assertEqual(self.calls(), [])

    def test_event_without_cwd_uses_project_dir_then_process_cwd(self):
        self.set_pr([self.review_comment()])
        r = self.hook(event={"cwd": DROP}, env=dict(self.env, CLAUDE_PROJECT_DIR=str(self.repo)))
        self.assertLimited(r, "1-ko'rigi toza")
        r = self.hook(event={"cwd": None}, env=dict(self.env, CLAUDE_PROJECT_DIR=str(self.repo)), run_cwd=self.tmp)
        self.assertLimited(r, "1-ko'rigi toza")
        r = self.hook(event={"cwd": DROP}, run_cwd=self.repo)
        self.assertLimited(r, "1-ko'rigi toza")
        self.assertPasses(self.hook(event={"cwd": DROP}, run_cwd=self.tmp))  # na hook cwd, na loyiha papkasi repo emas


class StubStampTests(HookTestCase):
    """Soxta review_stamp.py bilan: hook uni qanday chaqiradi va chiqish kodlarini qanday talqin qiladi."""

    def setUp(self):
        super().setUp()
        self.hook_path = self.make_layout("stub", stamp="stub", config=EXAMPLE)
        self.log = self.tmp / "stub.log"
        self.stub_env = dict(self.env, STUB_LOG=str(self.log))

    def calls_of_stub(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def run_stub(self, exit_code, stderr="", **kw):
        return self.hook(hook=self.hook_path, env=dict(self.stub_env, STUB_EXIT=str(exit_code), STUB_STDERR=stderr), **kw)

    def test_only_exit_code_4_blocks(self):
        for code in (0, 1, 2, 3, 5, 64, 127, 255):
            with self.subTest(code=code):
                self.assertPasses(self.run_stub(code, "ko'rik turlari chegarasi: nimadir\n"))
        r = self.run_stub(4, "ko'rik turlari chegarasi: PR #7 sabab matni\n")
        self.assertLimited(r, "PR #7 sabab matni")

    def test_script_gets_pr_number_and_check_flag_in_the_repo_dir(self):
        self.run_stub(0)
        (call,) = self.calls_of_stub()
        self.assertEqual(call["argv"], ["--pr", "7", "--check"])
        self.assertEqual(Path(call["cwd"]).resolve(), self.repo)

    def test_script_runs_in_the_submodule_dir_when_the_brief_names_it(self):
        sub = self.repo / SUB
        sub.mkdir(parents=True)
        self.run_stub(0, prompt=SUB_BRIEF)
        (call,) = self.calls_of_stub()
        self.assertEqual(Path(call["cwd"]).resolve(), sub.resolve())

    def test_config_environment_variable_reaches_the_script(self):
        self.run_stub(0)
        (call,) = self.calls_of_stub()
        self.assertEqual(call["qc"], str(self.tmp / "config.json"))

    def test_config_argument_of_the_hook_is_passed_on(self):
        other = self.tmp / "other.json"
        other.write_text(json.dumps(EXAMPLE))
        self.hook(hook=self.hook_path, env=dict(self.stub_env), args=("--config", str(other)))
        self.hook(hook=self.hook_path, env=dict(self.stub_env), args=(f"--config={other}",))
        first, second = self.calls_of_stub()
        self.assertEqual(first["argv"], ["--pr", "7", "--check", "--config", str(other)])
        self.assertEqual(second["argv"], first["argv"])

    def test_relative_config_paths_are_made_absolute_for_the_script(self):
        """Skript boshqa papkada (repo da) ishlaydi: hookning cwd siga nisbatan nisbiy yo'l o'sha faylga ishora qilib qolishi shart."""
        (self.tmp / "rel.json").write_text(json.dumps(EXAMPLE))
        self.hook(hook=self.hook_path, env=dict(self.stub_env), args=("--config", "rel.json"), run_cwd=self.tmp)
        self.hook(hook=self.hook_path, env=dict(self.stub_env, QUALITY_CONFIG="rel.json"), run_cwd=self.tmp)
        by_arg, by_env = self.calls_of_stub()
        self.assertEqual(by_arg["argv"], ["--pr", "7", "--check", "--config", str(self.tmp / "rel.json")])
        self.assertEqual(by_env["qc"], str(self.tmp / "rel.json"))
        self.assertNotIn("--config", by_env["argv"])

    def test_without_config_argument_none_is_passed(self):
        self.run_stub(0)
        (call,) = self.calls_of_stub()
        self.assertNotIn("--config", call["argv"])

    def test_config_warning_lines_of_the_script_are_not_part_of_the_block_message(self):
        r = self.run_stub(4, "ogohlantirish: config.json o'qilmadi\nko'rik turlari chegarasi: PR #7 sabab\n")
        self.assertLimited(r, "PR #7 sabab")
        self.assertNotIn("ogohlantirish", r.stderr)

    def test_script_that_writes_to_stdout_does_not_leak_into_the_hook_output(self):
        executable(self.hook_path.parent.parent / "quality" / "review_stamp.py", "#!/bin/sh\necho noise\nexit 0\n")
        self.assertPasses(self.hook(hook=self.hook_path))

    def test_unrunnable_script_passes(self):
        (self.hook_path.parent.parent / "quality" / "review_stamp.py").write_text("not a program")  # exec biti bor, mazmuni yaroqsiz python
        self.assertPasses(self.hook(hook=self.hook_path))


class SubmoduleBriefTests(HookTestCase):
    """Brifda config `submodules` dagi papka tilga olinsa tekshiruv o'sha papkaning o'z repo sida yuradi (PR lari va ruxsat izi o'zniki)."""

    def setUp(self):
        super().setUp()
        self.sub = self.repo / "libs" / "shared"
        self.sub2 = self.repo / "libs" / "extra"
        for d in (self.sub, self.sub2):
            shutil.copytree(TEMPLATE, d, symlinks=True)
        self.set_pr([self.review_comment()], name="shared")  # loyiha repo ning pr.json i izohsiz

    def test_brief_naming_submodule_checks_the_submodule_repo(self):
        self.assertLimited(self.hook(prompt=SUB_BRIEF), "1-ko'rigi toza")
        self.assertCheckedIn("shared")

    def test_brief_without_submodule_checks_main_repo(self):
        self.assertPasses(self.hook())
        self.assertCheckedIn("repo")

    def test_second_configured_submodule(self):
        self.set_pr([self.review_comment()], name="extra")
        self.assertLimited(self.hook(prompt=f"PR #7, repo papkasi {SUB2}"), "1-ko'rigi toza")
        self.assertCheckedIn("extra")

    def test_missing_submodule_dir_falls_back_to_main_repo(self):
        shutil.rmtree(self.sub)
        self.assertPasses(self.hook(prompt=SUB_BRIEF))
        self.assertCheckedIn("repo")

    def test_submodule_uses_its_own_allow_file(self):
        self.set_pr([self.review_comment(blockers=1), self.review_comment(round=2)], name="shared")
        self.allow_file(self.repo).write_text("7 3 t\n")  # loyiha repo ruxsati submodule PR iga tegishli emas
        self.assertLimited(self.hook(prompt=SUB_BRIEF), THIRD)
        self.allow_file(self.sub).write_text("7 3 t\n")
        self.assertPasses(self.hook(prompt=SUB_BRIEF))

    def test_path_continuations_are_not_repo_mentions(self):
        """`<papka>/fayl`, `<papka>.md`, `<papka>-eski`, `<papka>_x`, `x<papka>`: bu fayl yoki boshqa papka havolasi, submodule PR i emas."""
        for text in (f"{SUB}/README.md", f"{SUB}/", f"{SUB}.md", f"{SUB}.git", f"{SUB}-old", f"{SUB}_x", f"{SUB}2", f"x{SUB}", f"old-{SUB}", f"a.{SUB}"):
            with self.subTest(text=text):
                (self.gh_dir / "calls.log").unlink(missing_ok=True)
                self.assertPasses(self.hook(prompt=f"PR #7 ni ko'rib chiq: {text} ga qarang"))
                self.assertCheckedIn("repo")

    def test_mentions_that_name_the_repo(self):
        for text in (f"repo papkasi {SUB}", f"repo papkasi {SUB}.", f"repo papkasi {SUB}, PR #7", f"'{SUB}'", f"({SUB})", f'"{SUB}"',
                     f"{SUB}\nkeyingi qator", f"/abs/path/to/{SUB}", f"./{SUB}", f"{SUB}: ko'rik", f"`{SUB}`", f"{SUB}!", f"{SUB}?"):
            with self.subTest(text=text):
                (self.gh_dir / "calls.log").unlink(missing_ok=True)
                self.assertLimited(self.hook(prompt=f"PR #7 ni ko'rib chiq: {text}"))
                self.assertCheckedIn("shared")

    def test_directory_that_is_not_in_the_config_is_never_used(self):
        other = self.repo / "libs" / "other"
        shutil.copytree(TEMPLATE, other, symlinks=True)
        self.set_pr([self.review_comment()], name="other")
        self.assertPasses(self.hook(prompt="PR #7, repo papkasi libs/other"))
        self.assertCheckedIn("repo")

    def test_submodule_names_with_slashes_or_blanks_are_normalised(self):
        self.set_config({"prefix": PREFIX, "reviewer": REVIEWER, "submodules": [f"{SUB}/", "", " ", "/"]})
        self.assertLimited(self.hook(prompt=SUB_BRIEF), "1-ko'rigi toza")
        self.assertCheckedIn("shared")

    def test_no_submodules_in_config_means_main_repo_always(self):
        self.set_config({"prefix": PREFIX, "reviewer": REVIEWER})
        self.assertPasses(self.hook(prompt=SUB_BRIEF))
        self.assertCheckedIn("repo")

    def test_submodule_name_with_regex_characters_is_literal(self):
        odd = self.repo / "vendor" / "a+b"
        shutil.copytree(TEMPLATE, odd, symlinks=True)
        self.set_config({"prefix": PREFIX, "reviewer": REVIEWER, "submodules": ["vendor/a+b"]})
        self.set_pr([self.review_comment()], name="a+b")
        self.assertLimited(self.hook(prompt="PR #7 repo papkasi vendor/a+b"))
        self.assertCheckedIn("a+b")
        (self.gh_dir / "calls.log").unlink()
        self.assertPasses(self.hook(prompt="PR #7 repo papkasi vendor/aab"))
        self.assertCheckedIn("repo")


class ConfigTests(HookTestCase):
    """config.json: reviewer va submodules hookda, prefix va gate_command skriptda; `--config` > $QUALITY_CONFIG > <hook papkasi>/../quality/config.json."""

    def test_configured_reviewer_is_enforced_and_the_default_name_is_not(self):
        self.set_pr([self.review_comment()])
        self.assertLimited(self.hook(agent=REVIEWER))
        self.assertPasses(self.hook(agent=DEFAULT_REVIEWER))

    def test_prefix_from_config_decides_which_markers_are_rounds(self):
        self.set_pr([self.review_comment(prefix="acme")])
        self.assertLimited(self.hook())
        self.set_config({"prefix": "zeta", "reviewer": REVIEWER})
        self.assertPasses(self.hook())  # acme belgisi zeta uchun tur emas
        self.set_pr([self.review_comment(prefix="zeta")])
        self.assertLimited(self.hook())

    def test_gate_command_from_config_is_named_in_the_block_message(self):
        self.set_config({"prefix": PREFIX, "reviewer": REVIEWER, "gate_command": "just verify"})
        self.set_pr([self.review_comment()])
        self.assertLimited(self.hook(), "`just verify`")

    def test_unreadable_config_gives_defaults_and_one_warning_line(self):
        directory = self.tmp / "a-directory"
        directory.mkdir()
        self.set_pr([self.review_comment(prefix="quality")])
        for name, content in {"buzuq JSON": "{not json", "bo'sh fayl": "", "ro'yxat": "[]", "juda chuqur JSON": "[" * 100000, "papka": directory}.items():
            with self.subTest(name):
                if isinstance(content, Path):
                    self.env["QUALITY_CONFIG"] = str(content)
                else:
                    self.set_config(content, "bad.json")
                r = self.hook(agent=DEFAULT_REVIEWER)
                lines = [ln for ln in r.stderr.splitlines() if ln.strip()]
                warnings = [ln for ln in lines if ln.startswith("ogohlantirish:")]
                self.assertEqual(r.returncode, 2, r.stderr)  # standart reviewer va prefix: toza 1-turdan keyin 2-tur yopiq
                self.assertEqual(len(warnings), 1, r.stderr)  # skriptning ogohlantirishi blok xabariga qo'shilmaydi
                self.assertEqual(len(lines), 2, r.stderr)
                self.assertTrue(lines[1].startswith("Ko'rik bloklandi: "), lines)
                r = self.hook(agent=REVIEWER)
                self.assertEqual(r.returncode, 0, r.stderr)

    def test_wrongly_typed_keys_fall_back_per_key(self):
        self.set_config({"reviewer": 5, "submodules": "libs/shared", "prefix": PREFIX})
        self.set_pr([self.review_comment()])
        r = self.hook(agent=DEFAULT_REVIEWER, prompt=SUB_BRIEF)
        self.assertEqual(r.returncode, 2, r.stderr)
        warned = sorted(re.search(r"`(\w+)`", ln).group(1) for ln in r.stderr.splitlines() if ln.startswith("ogohlantirish:"))
        self.assertEqual(warned, ["reviewer", "submodules"])

    def test_null_values_mean_unset_without_a_warning(self):
        self.set_config({"reviewer": None, "submodules": None, "prefix": None})
        self.set_pr([self.review_comment(prefix="quality")])
        self.assertLimited(self.hook(agent=DEFAULT_REVIEWER))

    def test_other_keys_in_the_file_are_ignored(self):
        self.set_config({"ref": "origin/main", "baseline": "x", "protected": ["main"], "quality_log": {}, "reviewer": REVIEWER, "prefix": PREFIX})
        self.set_pr([self.review_comment()])
        self.assertLimited(self.hook())

    def test_environment_variable_beats_the_installed_config_file(self):
        hook = self.make_layout("inst", config={"reviewer": "from-file", "prefix": "file"})
        self.set_pr([self.review_comment()])
        self.assertLimited(self.hook(hook=hook, agent=REVIEWER))  # env: EXAMPLE
        self.assertPasses(self.hook(hook=hook, agent="from-file"))

    def test_config_argument_beats_the_environment_variable(self):
        hook = self.make_layout("inst", config={"reviewer": "from-file", "prefix": "file"})
        by_arg = self.tmp / "arg.json"
        by_arg.write_text(json.dumps({"reviewer": "from-arg", "prefix": "arg"}))
        self.set_pr([self.review_comment(prefix="arg")])
        self.assertLimited(self.hook(hook=hook, agent="from-arg", args=("--config", str(by_arg))))
        self.assertPasses(self.hook(hook=hook, agent=REVIEWER, args=("--config", str(by_arg))))


class InstalledLayoutTests(HookTestCase):
    """O'rnatilgan joylashuv (hooks/ va quality/ yonma-yon) nusxalari bilan: config va skript hook papkasiga nisbatan topiladi."""

    def test_config_is_read_from_the_quality_dir_next_to_the_hooks_dir(self):
        hook = self.make_layout("inst", config={"reviewer": "layout-critic", "prefix": "lay", "gate_command": "make lay"})
        env = self.env_without_config()
        self.set_pr([self.review_comment(prefix="lay")])
        r = self.hook(hook=hook, agent="layout-critic", env=env)
        self.assertLimited(r, "`make lay`")  # reviewer ham, prefiks (skript) ham, gate_command ham o'sha config dan
        self.assertPasses(self.hook(hook=hook, agent=REVIEWER, env=env))
        self.set_pr([self.review_comment(prefix="acme")])
        self.assertPasses(self.hook(hook=hook, agent="layout-critic", env=env))  # boshqa prefiks belgisi tur emas

    def test_no_config_file_anywhere_gives_defaults(self):
        hook = self.make_layout("inst")
        env = self.env_without_config()
        self.set_pr([self.review_comment(prefix="quality")])
        r = self.hook(hook=hook, agent=DEFAULT_REVIEWER, env=env)
        self.assertLimited(r, "`make ci`")
        self.assertNotIn("ogohlantirish", r.stderr)
        self.assertPasses(self.hook(hook=hook, agent=REVIEWER, env=env))

    def test_kit_layout_with_both_files_side_by_side(self):
        hook = self.make_layout("kit", kit=True)
        env = self.env_without_config()
        self.set_pr([self.review_comment(prefix="quality")])
        self.assertLimited(self.hook(hook=hook, agent=DEFAULT_REVIEWER, env=env), "1-ko'rigi toza")

    def test_quality_dir_script_is_preferred_over_a_neighbour_copy(self):
        hook = self.make_layout("inst", config=EXAMPLE)
        executable(hook.parent / "review_stamp.py", "#!/bin/sh\nexit 0\n")  # hook yonidagi nusxa har doim 0 qaytaradi
        self.set_pr([self.review_comment()])
        self.assertLimited(self.hook(hook=hook))  # ../quality/ dagi haqiqiy skript ishladi
        shutil.rmtree(hook.parent.parent / "quality")
        self.assertPasses(self.hook(hook=hook))  # faqat yonidagi nusxa qoldi

    def test_hook_run_through_a_relative_path_finds_its_files(self):
        hook = self.make_layout("inst", config=EXAMPLE)
        self.set_pr([self.review_comment()])
        r = self.hook(hook=os.path.relpath(hook, self.repo), run_cwd=self.repo)
        self.assertLimited(r)


class DefaultConfigTests(HookTestCase):
    """Sozlamasiz (config.json yo'q): reviewer opus-reviewer, prefix quality, submodule yo'q."""
    CONFIG = None
    prefix = "quality"
    reviewer = DEFAULT_REVIEWER

    def test_default_reviewer_is_enforced_and_others_pass(self):
        self.set_pr([self.review_comment()])
        r = self.hook()
        self.assertLimited(r, "1-ko'rigi toza")
        self.assertIn("`make ci`", r.stderr)
        self.assertEqual(r.stderr.count("\n"), 1)
        self.assertPasses(self.hook(agent=REVIEWER))
        self.assertPasses(self.hook(agent="sonnet-coder"))

    def test_default_prefix_decides_the_markers(self):
        self.set_pr([self.review_comment(prefix=PREFIX)])
        self.assertPasses(self.hook())
        self.set_pr([self.review_comment(prefix="quality")])
        self.assertLimited(self.hook())

    def test_third_round_hint_and_allow_file_with_default_names(self):
        self.set_pr([self.review_comment(blockers=1), self.review_comment(round=2)])
        r = self.hook()
        self.assertLimited(r, THIRD)
        self.assertIn("--pr 7 --allow --round 3", r.stderr)
        self.allow_file(self.repo).write_text("7 3 t\n")
        self.assertEqual(self.allow_file(self.repo).name, "quality-review-allow")
        self.assertPasses(self.hook())

    def test_no_submodules_by_default(self):
        sub = self.repo / "libs" / "shared"
        shutil.copytree(TEMPLATE, sub, symlinks=True)
        self.set_pr([self.review_comment()], name="shared")
        self.assertPasses(self.hook(prompt=SUB_BRIEF))
        self.assertCheckedIn("repo")


class AllowCommandIntegrationTests(HookTestCase):
    """`review_stamp.py --allow` yozgan ruxsat izi hook o'qiydigan shaklda bo'lishi shart: aynan o'sha turni ochadi."""

    def allow(self, *extra, cwd=None, pr="7"):
        return subprocess.run([sys.executable, str(STAMP), "--pr", pr, "--allow", *extra], cwd=str(cwd or self.repo),
                              capture_output=True, text=True, env=self.env)

    def test_written_allow_opens_exactly_its_round_in_the_hook(self):
        self.set_pr([self.review_comment(blockers=1), self.review_comment(round=2, blockers=1)])
        self.assertLimited(self.hook(), THIRD)
        self.assertEqual(self.allow().returncode, 0)
        self.assertPasses(self.hook())
        self.set_pr([self.review_comment(blockers=1), self.review_comment(round=2, blockers=1), self.review_comment(round=3, blockers=1)])
        self.assertLimited(self.hook(), FOURTH)
        self.assertEqual(self.allow("--round", "4").returncode, 0)
        self.assertPasses(self.hook())

    def test_new_allow_replaces_the_previous_one(self):
        self.set_pr([self.review_comment(blockers=1), self.review_comment(round=2, blockers=1)])
        self.assertEqual(self.allow().returncode, 0)
        self.assertEqual(self.allow("--round", "5", pr="8").returncode, 0)
        self.assertTrue(self.allow_file(self.repo).read_text().startswith("8 5 "))
        self.assertLimited(self.hook(), THIRD)

    def test_allow_goes_to_the_repo_of_the_directory_it_runs_in(self):
        sub = self.repo / "libs" / "shared"
        shutil.copytree(TEMPLATE, sub, symlinks=True)
        self.set_pr([self.review_comment(blockers=1), self.review_comment(round=2, blockers=1)], name="shared")
        self.assertEqual(self.allow(cwd=self.repo).returncode, 0)
        self.assertLimited(self.hook(prompt=SUB_BRIEF), THIRD)
        self.assertEqual(self.allow(cwd=sub).returncode, 0)
        self.assertPasses(self.hook(prompt=SUB_BRIEF))

    def test_hook_check_does_not_consume_the_allow(self):
        self.set_pr([self.review_comment(blockers=1), self.review_comment(round=2, blockers=1)])
        self.allow()
        for _ in range(3):
            self.assertPasses(self.hook())
        self.assertTrue(self.allow_file(self.repo).exists())


if __name__ == "__main__":
    unittest.main()
