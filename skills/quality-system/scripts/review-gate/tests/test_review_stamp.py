#!/usr/bin/env python3
"""review_stamp.py testlari: mustaqil ko'rik izi, ko'rik turlari chegarasi, --allow, config.json.

Qora quti: skript subprocess orqali ishga tushadi, git repo lar vaqtinchalik papkada (`origin/<base>` ref bilan),
gh o'rniga PATH ning boshidagi soxta skript (haqiqiy GitHub ga tegilmaydi), sozlama QUALITY_CONFIG orqali vaqtinchalik JSON.
Ikki joylashuvni biladi: to'plam papkasi (review_stamp.py tests/ ning ota papkasida) va o'rnatilgan loyiha (.claude/quality/tests).

  python3 -m unittest discover -s .claude/quality/tests -p 'test_review_*.py' -v
"""
import atexit
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
SCRIPT = QUALITY / "review_stamp.py"

# Sozlama misoli: kit isbotlangan loyihaning iz nomlarini aynan takrorlaydi (prefix "sos", yorliq "Manba"); standartlar generik
PREFIX = "sos"
LABELS = ["Manba", "Source"]
SENSITIVE = [r"^src/auth/", r"(?:^|/)migrations/", r"^config/(?:secrets|session)\.yml$", r"\.pem$", r"^\.claude/"]
EXAMPLE = {"prefix": PREFIX, "gate_command": "make ci", "source_labels": LABELS, "sensitive": SENSITIVE}

HOME = tempfile.mkdtemp(prefix="stamp-home-")
atexit.register(shutil.rmtree, HOME, ignore_errors=True)  # modul import qilinib testlari ishga tushmasa ham qolmasin
BASE_ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_") and k not in ("CLAUDE_PROJECT_DIR", "QUALITY_CONFIG")}
BASE_ENV.update(
    HOME=HOME, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1",
    GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com",
)
if sys.pycache_prefix:  # tizim Python ida bayt-kod keshi HOME ga bog'liq: HOME almashgach har bola jarayon stdlib ni qaytadan kompilyatsiya qilardi
    BASE_ENV["PYTHONPYCACHEPREFIX"] = sys.pycache_prefix

# Soxta gh (sh: python dan tezroq): `pr view N` FAKE_GH_DIR/pr<N>.json ni (yo'q bo'lsa pr.json ni) qaytaradi,
# `pr comment --body-file F` F ni comment.md ga ko'chiradi; hamma chaqiruv calls.log da
FAKE_GH = """#!/bin/sh
d="$FAKE_GH_DIR"
printf '%s\\n' "$*" >> "$d/calls.log"
if [ "$1" = pr ] && [ "$2" = view ]; then
  p="$d/pr$3.json"
  [ -f "$p" ] || p="$d/pr.json"
  if [ ! -f "$p" ]; then echo "no pull requests found" >&2; exit 1; fi
  cat "$p"
elif [ "$1" = pr ] && [ "$2" = comment ]; then
  if [ -f "$d/comment_fail" ]; then echo "HTTP 502" >&2; exit 1; fi
  while [ $# -gt 0 ]; do
    if [ "$1" = --body-file ]; then cp "$2" "$d/comment.md"; break; fi
    shift
  done
else
  exit 64
fi
"""

REPORT = "# Ko'rik\n\nHammasi joyida.\n\nVERDICT: blockers=0 major=1 minor=2 security=n/a\n"
CLEAN = "Hammasi joyida.\n\nVERDICT: blockers=0 major=0 minor=0 security=n/a\n"
DONE = "Xavfsizlik ham ko'rildi.\n\nVERDICT: blockers=0 major=0 minor=0 security=done\n"
BLOCKED = "Topilma bor.\n\nVERDICT: blockers=1 major=0 minor=0 security=n/a\n"
MARKER_HEAD, MARKER_TREE = "1" * 40, "2" * 40  # izohdagi belgi uchun soxta qiymatlar: skript ularni faqat matn deb o'qiydi
TIME = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")


TEMPLATE = Path(HOME) / "template-repo"  # main + origin/main + feature (change.txt): setUp bunga nusxa oladi (git chaqiruvlaridan tezroq)
TEMPLATE_HEAD = None


def setUpModule():
    global TEMPLATE_HEAD
    TEMPLATE_HEAD = make_repo(TEMPLATE, "change.txt")


def tearDownModule():
    shutil.rmtree(HOME, ignore_errors=True)


def git(cwd, *args, check=True):
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, env=BASE_ENV)
    if check and r.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} ({cwd}): {r.stderr}")
    return r.stdout.strip()


def commit(repo, name, text=None):
    p = Path(repo) / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text or name)
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", name)
    return git(repo, "rev-parse", "HEAD")


def commit_files(repo, names):
    """Bitta commitda ko'p fayl (tezroq): qaytaradi yangi HEAD."""
    for name in names:
        p = Path(repo) / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(name)
    git(repo, "add", *names)
    git(repo, "commit", "-q", "-m", "files")
    return git(repo, "rev-parse", "HEAD")


def make_repo(repo, *files, base="main", remote="origin", base_files=()):
    """repo: base da bitta commit (+base_files), `<remote>/<base>` shu commitga ishora qiladi, feature da files; qaytaradi: feature head."""
    git(Path(repo).parent, "init", "-q", "-b", base, str(repo))
    commit(repo, "base.txt")
    for f in base_files:
        commit(repo, f)
    git(repo, "update-ref", f"refs/remotes/{remote}/{base}", "HEAD")
    git(repo, "switch", "-q", "-c", "feature")
    head = git(repo, "rev-parse", "HEAD")
    for f in files:
        head = commit(repo, f)
    return head


class StampTestCase(unittest.TestCase):
    CONFIG = EXAMPLE  # None: config fayli yo'q (standart qiymatlar)
    prefix = PREFIX
    label = LABELS[0]

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="stamp-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.gh_dir = self.tmp / "gh"
        (self.gh_dir / "bin").mkdir(parents=True)
        fake = self.gh_dir / "bin" / "gh"
        fake.write_text(FAKE_GH)
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        self.env = dict(BASE_ENV, FAKE_GH_DIR=str(self.gh_dir), PATH=f"{self.gh_dir / 'bin'}{os.pathsep}{os.environ['PATH']}")
        self.set_config(self.CONFIG)
        self.body = f"## Xulosa\nNimadir.\n\n{self.label}: docs/spec.md 3-bo'lim\n"
        self.repo = self.tmp / "repo"
        shutil.copytree(TEMPLATE, self.repo, symlinks=True)
        self.head = TEMPLATE_HEAD
        self.report = self.tmp / "report.md"
        self.report.write_text(REPORT)
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

    def make_branch(self, repo, *files, **kw):
        return make_repo(repo, *files, **kw)

    def branch_with(self, *files, base_files=()):
        """Yangi repo: feature da bitta commit shu fayllar bilan (bo'lmasa feature = base); base_files base da bor fayllar."""
        shutil.rmtree(self.repo)
        self.head = self.make_branch(self.repo, base_files=base_files)
        if files:
            self.head = commit_files(self.repo, list(files))
        else:
            self.head = git(self.repo, "rev-parse", "HEAD")
        self.set_pr()

    def set_pr(self, head=None, number=7, **over):
        """PR 7 pr.json da, boshqa raqamlar pr<N>.json da (soxta gh `pr view N` da shuni qidiradi)."""
        pr = {"number": number, "state": "OPEN", "headRefOid": head or self.head, "body": self.body, "baseRefName": "main", "comments": []}
        pr.update(over)
        (self.gh_dir / ("pr.json" if number == 7 else f"pr{number}.json")).write_text(json.dumps(pr))

    def run_script(self, *args, cwd=None, env=None):
        return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=str(cwd or self.repo), capture_output=True, text=True, env=env or self.env)

    def stamp(self, report=None, cwd=None, pr=7, head=None, extra=(), env=None):
        if report is not None:
            self.report.write_text(report)
        return self.run_script("--pr", str(pr), "--head", head or self.head, "--report", str(self.report), *extra, cwd=cwd, env=env)

    def check(self, cwd=None, pr=7, env=None):
        return self.run_script("--pr", str(pr), "--check", cwd=cwd, env=env)

    def allow(self, *extra, cwd=None, pr=7, env=None):
        return self.run_script("--pr", str(pr), "--allow", *extra, cwd=cwd, env=env)

    def git_file(self, name, repo=None):
        repo = Path(repo or self.repo)
        return repo / git(repo, "rev-parse", "--git-path", name)  # nisbiy yo'l repo ga nisbatan, mutlaq yo'l o'zi

    def stamp_file(self, repo=None, pr=7):
        return self.git_file(f"{self.prefix}-review-ok-{pr}", repo)

    def legacy_stamp_file(self, repo=None):
        """PR bo'yicha izlardan oldingi umumiy fayl: skript endi uni yozmaydi."""
        return self.git_file(f"{self.prefix}-review-ok", repo)

    def stamp_names(self, repo=None):
        """Git papkadagi barcha `<prefix>-review-ok*` fayllari (vaqtinchalik `.tmp` ham): PR bo'yicha izlar va boshqa hech narsa."""
        return sorted(p.name for p in self.stamp_file(repo).parent.glob(f"{self.prefix}-review-ok*"))

    def allow_file(self, repo=None):
        return self.git_file(f"{self.prefix}-review-allow", repo)

    def write_allow(self, text="7 3 2026-11-04T10:00:00Z\n", repo=None):
        """Ruxsat izini qo'lda yozadi (`--allow` yozadigan shaklda yoki buzuq mazmun bilan); izning yo'lini qaytaradi."""
        p = self.allow_file(repo)
        if isinstance(text, bytes):
            p.write_bytes(text)
        else:
            p.write_text(text)
        return p

    def comment(self):
        p = self.gh_dir / "comment.md"
        return p.read_text() if p.exists() else None

    def marker(self, blockers, major, minor, round=None, security="n/a"):
        """PR izohi (`gh pr view --json comments` elementi): hisobot va tur belgisi; round=None: `round=` siz eski belgi."""
        tail = f" round={round}" if round is not None else ""
        verdict = f"blockers={blockers} major={major} minor={minor} security={security}"
        return {"body": f"Hisobot.\n\nVERDICT: {verdict}\n\n<!-- {self.prefix}-review head={MARKER_HEAD} tree={MARKER_TREE} {verdict}{tail} -->\n"}

    def stamp_tail(self):
        """Iz qatorining `<daraxt> <PR> <head> <vaqt>` dan keyingi qismi: [round=K, blockers=B, major=M, minor=N]."""
        return self.stamp_file().read_text().split()[4:]

    def gh_calls(self):
        p = self.gh_dir / "calls.log"
        return p.read_text().splitlines() if p.exists() else []

    def comment_calls(self):
        return sum(1 for line in self.gh_calls() if line.startswith("pr comment"))

    def reset(self):
        """subTest lar orasida: iz, ruxsat, izoh va gh chaqiruvlari jurnali tozalanadi."""
        for p in (self.stamp_file(), self.allow_file(), self.gh_dir / "comment.md", self.gh_dir / "calls.log"):
            p.unlink(missing_ok=True)

    def assertRefused(self, r, text=None):
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("ko'rik izi yozilmadi", r.stderr)
        if text:
            self.assertIn(text, r.stderr)
        self.assertFalse(self.stamp_file().exists())
        self.assertIsNone(self.comment())

    def assertNothingWritten(self):
        self.assertFalse(self.stamp_file().exists())
        self.assertIsNone(self.comment())
        self.assertEqual(self.comment_calls(), 0)

    def assertLimited(self, r, *texts):
        self.assertEqual(r.returncode, 4, r.stderr)
        self.assertTrue(r.stderr.startswith("ko'rik turlari chegarasi:"), r.stderr)
        for text in texts:
            self.assertIn(text, r.stderr)
        self.assertNothingWritten()


class HappyPathTests(StampTestCase):
    def test_script_is_an_executable_python_program(self):
        self.assertTrue(os.access(SCRIPT, os.X_OK))
        text = SCRIPT.read_text()
        self.assertTrue(text.startswith("#!/usr/bin/env python3\n"))
        compile(text, str(SCRIPT), "exec")

    def test_writes_stamp_and_posts_comment_with_marker(self):
        r = self.stamp()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr, "")
        tree = git(self.repo, "rev-parse", f"{self.head}^{{tree}}")
        text = self.stamp_file().read_text()
        fields = text.split()
        self.assertEqual(fields[:3], [tree, "7", self.head])
        self.assertRegex(fields[3], f"^{TIME.pattern}$")
        self.assertEqual(fields[4:], ["round=1", "blockers=0", "major=1", "minor=2"])
        self.assertTrue(text.endswith("\n") and text.count("\n") == 1)
        body = self.comment()
        self.assertIn("Hammasi joyida.", body)
        self.assertEqual(
            body.rstrip("\n").splitlines()[-1],
            f"<!-- {self.prefix}-review head={self.head} tree={tree} blockers=0 major=1 minor=2 security=n/a round=1 -->")
        calls = (self.gh_dir / "calls.log").read_text()
        self.assertIn("pr view 7 --json number,state,headRefOid,body,baseRefName,comments", calls)
        self.assertEqual(calls.count("pr view"), 1)
        self.assertIn("pr comment 7 --body-file", calls)

    def test_stamp_is_named_after_prefix_and_pr_only(self):
        self.assertEqual(self.stamp().returncode, 0)
        self.assertEqual(self.stamp_names(), [f"{self.prefix}-review-ok-7"])
        self.assertFalse(self.legacy_stamp_file().exists())
        self.assertFalse(self.allow_file().exists())

    def test_output_names_the_stamp_path(self):
        r = self.stamp()
        self.assertIn(f"PR #7, head {self.head[:7]}", r.stdout)
        self.assertIn(str(self.stamp_file()), r.stdout)

    def test_stale_tmp_file_is_replaced_and_leaves_nothing(self):
        stale = Path(str(self.stamp_file()) + ".tmp")
        stale.write_text("junk")
        self.assertEqual(self.stamp().returncode, 0)
        self.assertEqual(self.stamp_names(), [f"{self.prefix}-review-ok-7"])

    def test_report_without_trailing_newline_and_blank_lines_after_verdict(self):
        r = self.stamp("x\n\nVERDICT: blockers=0 major=0 minor=0 security=done\n\n\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.stamp_file().exists())
        r = self.stamp("x\n\nVERDICT: blockers=0 major=0 minor=0 security=done")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_verdict_without_security_defaults_to_na(self):
        r = self.stamp("Hisobot.\n\nVERDICT: blockers=0 major=1 minor=2\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.comment().rstrip("\n").endswith(" blockers=0 major=1 minor=2 security=n/a round=1 -->"))
        self.assertEqual(self.stamp_tail(), ["round=1", "blockers=0", "major=1", "minor=2"])

    def test_security_done_is_written_into_the_marker(self):
        self.assertEqual(self.stamp(DONE).returncode, 0)
        self.assertTrue(self.comment().rstrip("\n").endswith(" blockers=0 major=0 minor=0 security=done round=1 -->"))

    def test_uppercase_head_is_accepted(self):
        r = self.stamp(head=self.head.upper())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.stamp_file().read_text().split()[2], self.head)

    def test_source_line_with_text_and_html_comment_is_accepted(self):
        L = self.label
        for body in (f"{L}: TZ 4.2 <!-- izoh -->\n", f"<!-- bosh -->\n{L}: <!-- a --> TZ 4.2\n", f"{L}:\n<!-- x -->\n{L}: ADR-012\n",
                     f"x\r\n{L}: TZ 4.2\r\n", f"  {L}: TZ 4.2\n", f"> {L}: TZ 4.2\n"):
            with self.subTest(body=body):
                self.reset()
                self.set_pr(body=body)
                r = self.stamp()
                self.assertEqual(r.returncode, 0, r.stderr)

    def test_bold_source_line_is_accepted(self):
        L = self.label
        for body in (f"x\n- **{L}:** TZ 4.2\n", f"**{L}**: TZ 4.2\n", f"__{L}:__ TZ 4.2\n"):
            with self.subTest(body=body):
                self.reset()
                self.set_pr(body=body)
                self.assertEqual(self.stamp().returncode, 0)

    def test_help_works(self):
        r = self.run_script("--help")
        self.assertEqual(r.returncode, 0)
        for option in ("--report", "--check", "--allow", "--round", "--config"):
            self.assertIn(option, r.stdout)

    def test_overwrites_previous_stamp(self):
        self.stamp_file().write_text("old 1 old 2026-01-01T00:00:00Z\n")
        self.assertEqual(self.stamp().returncode, 0)
        self.assertEqual(self.stamp_file().read_text().split()[2], self.head)

    def test_success_output_names_the_round(self):
        r = self.stamp()  # REPORT: muhim bor
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("1-tur", r.stdout)
        self.assertIn("2-ko'rik shart", r.stdout)
        self.reset()
        r = self.stamp("VERDICT: blockers=0 major=0 minor=2 security=n/a\n")
        self.assertIn("1-tur", r.stdout)
        self.assertNotIn("2-ko'rik shart", r.stdout)
        self.reset()
        self.set_pr(comments=[self.marker(0, 1, 0, 1)])
        r = self.stamp(REPORT)  # 2-turda ham muhim bor: eslatma faqat 1-tur uchun
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("2-tur", r.stdout)
        self.assertNotIn("2-ko'rik shart", r.stdout)


class RefusalTests(StampTestCase):
    def test_head_mismatch(self):
        self.set_pr(head="b" * 40)
        self.assertRefused(self.stamp(), "o'zgargan")

    def test_closed_and_merged_pr(self):
        for state in ("CLOSED", "MERGED"):
            with self.subTest(state=state):
                self.set_pr(state=state)
                self.assertRefused(self.stamp(), "ochiq emas")

    def test_base_not_the_base_branch(self):
        self.set_pr(baseRefName="develop")
        r = self.stamp()
        self.assertRefused(r, "base")
        self.assertIn("main", r.stderr)

    def test_missing_source_line(self):
        L = self.label
        for body in (f"{L} yo'q\n", f"{L}:\n", "", None, f"Faqat {L} so'zi: yo'q\n", "## Xulosa\nNimadir.\n",
                     f"{L}: <!-- majburiy: bo'lim yoki issue -->\n", f"{L}:<!-- a -->   <!-- b -->\n",
                     f"<!--\n{L}: TZ 4.2\n-->\n", f"{L}: <!-- yopilmagan\n"):
            with self.subTest(body=body):
                self.set_pr(body=body)
                self.assertRefused(self.stamp(), f"{L}:")

    def test_malformed_verdict(self):
        bad = [
            "",
            "faqat matn\n",
            "VERDICT: blockers=0 major=0 minor=0 security=done\nso'nggi qator boshqa\n",
            "VERDICT: blockers=0 major=0\n",
            "VERDICT: blockers=0 minor=0 major=0 security=done\n",
            "VERDICT: blockers=0 major=0 minor=0 security=yes\n",
            "VERDICT: blockers=0 major=0 minor=0 security=\n",
            "VERDICT: blockers=x major=0 minor=0 security=done\n",
            "VERDICT: blockers=-1 major=0 minor=0 security=done\n",
            "VERDICT: blockers=0 major=0 minor=0 security=done extra\n",
            "**VERDICT: blockers=0 major=0 minor=0 security=done**\n",
            "verdict: blockers=0 major=0 minor=0 security=done\n",
        ]
        for text in bad:
            with self.subTest(report=text):
                self.assertRefused(self.stamp(text), "VERDICT")

    def test_missing_report_file(self):
        self.report.unlink()
        self.assertRefused(self.stamp(), "hisobot o'qilmadi")

    def test_binary_report_file_is_a_refusal_not_a_crash(self):
        self.report.write_bytes(b"\xff\xfe\x00VERDICT")
        r = self.stamp()
        self.assertRefused(r, "hisobot o'qilmadi")
        self.assertNotIn("Traceback", r.stderr)

    def test_short_sha_is_refused(self):
        self.assertRefused(self.stamp(head=self.head[:10]), "40 belgili")
        self.assertRefused(self.stamp(head=self.head + "0"), "40 belgili")
        self.assertRefused(self.stamp(head="main"), "40 belgili")

    def test_commit_not_local_asks_for_fetch(self):
        missing = "a" * 40
        self.set_pr(head=missing)
        self.assertRefused(self.stamp(head=missing), "git fetch")

    def test_gh_pr_view_failure(self):
        (self.gh_dir / "pr.json").unlink()
        self.assertRefused(self.stamp(), "gh pr view")

    def test_gh_not_installed_is_a_refusal_not_a_crash(self):
        only_git = self.tmp / "only-git"
        only_git.mkdir()
        (only_git / "git").symlink_to(shutil.which("git"))
        r = self.stamp(env=dict(self.env, PATH=str(only_git)))
        self.assertRefused(r, "gh pr view")
        self.assertNotIn("Traceback", r.stderr)

    def test_gh_answer_must_be_a_json_object(self):
        for text in ("not json", "[]", "null", '"x"', "5"):
            with self.subTest(answer=text):
                (self.gh_dir / "pr.json").write_text(text)
                r = self.stamp()
                self.assertRefused(r, "JSON")
                self.assertNotIn("Traceback", r.stderr)

    def test_comments_must_be_a_list(self):
        for value in (None, {}, "x", 5):
            with self.subTest(comments=value):
                self.set_pr(comments=value)
                self.assertRefused(self.stamp(), "comments")
        pr = json.loads((self.gh_dir / "pr.json").read_text())
        del pr["comments"]  # maydon umuman yo'q
        (self.gh_dir / "pr.json").write_text(json.dumps(pr))
        self.assertRefused(self.stamp(), "comments")

    def test_comment_failure_writes_no_stamp(self):
        (self.gh_dir / "comment_fail").write_text("")
        r = self.stamp()
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("gh pr comment", r.stderr)
        self.assertFalse(self.stamp_file().exists())

    def test_missing_origin_main_asks_for_fetch(self):
        git(self.repo, "update-ref", "-d", "refs/remotes/origin/main")
        self.assertRefused(self.stamp(), "git fetch origin main")

    def test_refusal_keeps_existing_stamp(self):
        self.stamp_file().write_text("keep 7 keep 2026-01-01T00:00:00Z\n")
        self.set_pr(state="CLOSED")
        self.assertEqual(self.stamp().returncode, 1)
        self.assertTrue(self.stamp_file().exists())

    def test_stamp_mode_without_head_or_report_is_a_usage_error(self):
        for args in (["--pr", "7"], ["--pr", "7", "--head", self.head], ["--pr", "7", "--report", str(self.report)]):
            with self.subTest(args=args):
                r = self.run_script(*args)
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertIn("--head va --report kerak", r.stderr)
        for args in (["--check"], [], ["--pr", "x", "--check"], ["--pr", "0", "--check"], ["--pr", "-4", "--check"]):
            with self.subTest(args=args):
                self.assertEqual(self.run_script(*args).returncode, 2)
        self.assertEqual(self.gh_calls(), [])

    def test_abbreviated_options_are_not_accepted(self):
        for args in (["--pr", "7", "--he", self.head, "--rep", str(self.report)], ["--pr", "7", "--che"], ["--pr", "7", "--all"]):
            with self.subTest(args=args):
                self.assertEqual(self.run_script(*args).returncode, 2)
        self.assertEqual(self.gh_calls(), [])

    def test_not_a_git_repo(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        r = self.stamp(cwd=plain)
        self.assertEqual(r.returncode, 1)
        self.assertIn("git repo emas", r.stderr)


class BlockerTests(StampTestCase):
    def test_blockers_remove_old_stamp_and_exit_3(self):
        self.assertEqual(self.stamp().returncode, 0)
        self.assertTrue(self.stamp_file().exists())
        r = self.stamp("Topilma bor.\n\nVERDICT: blockers=2 major=0 minor=1 security=n/a\n")
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn("blokerlar bor: iz yozilmadi", r.stderr)
        self.assertFalse(self.stamp_file().exists())
        self.assertIn("blockers=2 major=0 minor=1", self.comment().rstrip().splitlines()[-1])

    def test_blockers_without_existing_stamp(self):
        r = self.stamp("VERDICT: blockers=1 major=0 minor=0 security=n/a\n")
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertFalse(self.stamp_file().exists())

    def test_blockers_remove_only_this_prs_stamp(self):
        other = "t 8 h 2026-01-01T00:00:00Z round=1 blockers=0 major=0 minor=0\n"
        self.stamp_file(pr=8).write_text(other)
        self.assertEqual(self.stamp().returncode, 0)
        self.assertEqual(self.stamp_names(), [f"{self.prefix}-review-ok-7", f"{self.prefix}-review-ok-8"])
        r = self.stamp(BLOCKED)
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertFalse(self.stamp_file(pr=7).exists())
        self.assertEqual(self.stamp_file(pr=8).read_text(), other)
        self.assertEqual(self.stamp_names(), [f"{self.prefix}-review-ok-8"])

    def test_unremovable_old_stamp_is_a_refusal_and_nothing_is_posted(self):
        """Eski iz o'chmasa merge hooki uni yaroqli deb olardi: jim o'tmaydi, izoh ham yozilmaydi."""
        self.stamp_file().mkdir()  # fayl o'rniga papka: o'chirib bo'lmaydi
        r = self.stamp(BLOCKED)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("eski iz o'chirilmadi", r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIsNone(self.comment())

    def test_blockers_do_not_need_the_source_line(self):
        self.set_pr(body="Boshqa matn\n")
        r = self.stamp(BLOCKED)
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIsNotNone(self.comment())

    def test_blockers_report_is_posted_with_a_round_marker(self):
        r = self.stamp(BLOCKED)
        self.assertEqual(r.returncode, 3, r.stderr)
        tree = git(self.repo, "rev-parse", f"{self.head}^{{tree}}")
        self.assertEqual(self.comment().rstrip("\n").splitlines()[-1],
                         f"<!-- {self.prefix}-review head={self.head} tree={tree} blockers=1 major=0 minor=0 security=n/a round=1 -->")
        self.assertIn("Topilma bor.", self.comment())

    def test_blockers_comment_failure_is_a_refusal_and_stamp_stays_removed(self):
        self.stamp_file().write_text("old 7 old 2026-01-01T00:00:00Z\n")
        (self.gh_dir / "comment_fail").write_text("")
        r = self.stamp(BLOCKED)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("gh pr comment", r.stderr)
        self.assertFalse(self.stamp_file().exists())


class RoundPolicyTests(StampTestCase):
    """Tur = PR izohlaridagi ko'rik belgilari soni + 1; 2-tur faqat 1-turda bloker yoki muhim bo'lsa, 3-tur va keyingilari ruxsat izi bilan."""

    # turlarni sanash

    def test_comments_without_marker_are_not_rounds(self):
        real = self.marker(1, 0, 0, 1)["body"]  # sanalsa 2-tur bo'lardi: round=2 yozilardi
        p = self.prefix
        cases = {
            "izoh yo'q": [],
            "belgisiz izohlar": [{"body": "Oddiy izoh."}, {"body": ""}, {"body": None}, {}, "satr", 5, None],
            "HTML izoh emas": [{"body": f"{p}-review head={MARKER_HEAD}"}, {"body": real.replace("<!-- ", "").replace(" -->", "")}],
            "belgi to'liq emas": [{"body": f"<!-- {p}-review head={MARKER_HEAD} -->"}, {"body": real.replace(f" tree={MARKER_TREE}", "")}],
            "qiymat noto'g'ri": [{"body": real.replace("security=n/a", "security=maybe")}, {"body": real.replace("blockers=1", "blockers=x")}],
            "boshqa prefiks": [{"body": real.replace(f"<!-- {p}-review", "<!-- other-review")}, {"body": real.replace(f"<!-- {p}-review", f"<!-- x{p}-review")}],
        }
        for name, comments in cases.items():
            with self.subTest(name):
                self.reset()
                self.set_pr(comments=comments)
                r = self.stamp()
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(self.stamp_tail()[0], "round=1")
        self.reset()
        self.set_pr(body=self.body + real)  # PR matnidagi belgi izoh emas
        self.assertEqual(self.stamp().returncode, 0)
        self.assertEqual(self.stamp_tail()[0], "round=1")

    def test_legacy_markers_without_round_count(self):
        self.set_pr(comments=[self.marker(1, 0, 0)])  # eski belgi: `round=` yo'q
        r = self.stamp()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.stamp_tail(), ["round=2", "blockers=0", "major=1", "minor=2"])
        self.reset()
        self.set_pr(comments=[self.marker(0, 0, 3)])
        self.assertLimited(self.stamp(), "1-ko'rigi toza")
        self.reset()
        self.set_pr(comments=[self.marker(1, 0, 0), self.marker(0, 1, 0, 2)])
        self.assertLimited(self.stamp(), "--pr 7 --allow --round 3")

    def test_only_marker_comments_count_and_are_listed_in_order(self):
        self.set_pr(comments=[{"body": "Odam izohi."}, self.marker(1, 0, 0, 1), {"body": "Yana izoh."}, self.marker(0, 2, 3, 2), {"body": "Oxirgisi."}])
        r = self.stamp()
        self.assertLimited(r, "--pr 7 --allow --round 3", "1-tur bloker 1 muhim 0 mayda 0", "2-tur bloker 0 muhim 2 mayda 3")
        self.assertLess(r.stderr.index("1-tur bloker"), r.stderr.index("2-tur bloker"))

    def test_two_markers_in_one_comment_count_once(self):
        self.set_pr(comments=[{"body": self.marker(1, 0, 0, 1)["body"] + self.marker(1, 0, 0, 2)["body"]}])
        r = self.stamp()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.stamp_tail()[0], "round=2")

    def test_marker_of_a_human_quote_inside_longer_text_counts(self):
        """Belgi izoh matnining istalgan joyida bo'lishi mumkin (hisobot + belgi bir izohda)."""
        body = "Oldingi matn.\n" + self.marker(0, 1, 0, 1)["body"] + "\nKeyingi matn.\n"
        self.set_pr(comments=[{"body": body}])
        r = self.stamp()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.stamp_tail()[0], "round=2")

    # 2-tur: faqat 1-turda bloker yoki muhim bo'lsa

    def test_second_round_allowed_after_blockers_or_major(self):
        for first in ((1, 0, 0), (0, 1, 0), (2, 3, 4)):
            with self.subTest(first=first):
                self.reset()
                self.set_pr(comments=[self.marker(*first, 1)])
                r = self.stamp()
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(self.stamp_tail(), ["round=2", "blockers=0", "major=1", "minor=2"])
                self.assertTrue(self.comment().rstrip("\n").endswith(" security=n/a round=2 -->"))

    def test_second_round_refused_after_clean_first_round(self):
        cases = {
            "mayda yo'q": self.marker(0, 0, 0, 1),
            "mayda bor": self.marker(0, 0, 5, 1),
            "eski belgi": self.marker(0, 0, 0),
            "security=done": self.marker(0, 0, 2, 1, "done"),
        }
        for name, first in cases.items():
            with self.subTest(name):
                self.reset()
                self.set_pr(comments=[first])
                self.assertLimited(self.stamp(), "1-ko'rigi toza")

    def test_clean_first_round_message_names_the_gate_and_forbids_a_rereview(self):
        self.set_pr(comments=[self.marker(0, 0, 0, 1)])
        r = self.stamp()
        self.assertLimited(r, "make ci", "qayta ko'rik yo'q", "PR #7")

    def test_limit_keeps_existing_stamp_and_drops_the_report(self):
        old = "t 7 h 2026-01-01T00:00:00Z round=1 blockers=0 major=0 minor=0\n"
        self.set_pr(comments=[self.marker(0, 0, 0, 1)])
        for report in (REPORT, BLOCKED):
            with self.subTest(verdict=report.splitlines()[-1]):
                self.stamp_file().write_text(old)
                r = self.stamp(report)
                self.assertEqual(r.returncode, 4, r.stderr)
                self.assertTrue(r.stderr.startswith("ko'rik turlari chegarasi:"), r.stderr)
                self.assertEqual(self.stamp_file().read_text(), old)
                self.assertIsNone(self.comment())
                self.assertEqual(self.comment_calls(), 0)

    # 3-tur va keyingilari: faqat ruxsat izi bilan

    def test_third_round_needs_allow_even_after_blockers(self):
        self.set_pr(comments=[self.marker(1, 0, 0, 1), self.marker(1, 2, 0, 2)])
        self.assertLimited(self.stamp(), "--pr 7 --allow --round 3", "foydalanuvchi ruxsati")

    def test_third_round_hint_is_a_command_usable_from_the_repo_dir(self):
        self.set_pr(comments=[self.marker(1, 0, 0, 1), self.marker(1, 0, 0, 2)])
        r = self.stamp()
        m = re.search(r"`python3 (\S+) --pr 7 --allow --round 3`", r.stderr)
        self.assertIsNotNone(m, r.stderr)
        self.assertEqual((self.repo / m.group(1)).resolve(), SCRIPT.resolve())

    def test_third_round_with_allow_writes_round_3_and_consumes_allow(self):
        self.set_pr(comments=[self.marker(1, 0, 0, 1), self.marker(0, 1, 0, 2)])
        allow = self.write_allow()
        r = self.stamp()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("3-tur", r.stdout)
        self.assertEqual(self.stamp_tail(), ["round=3", "blockers=0", "major=1", "minor=2"])
        self.assertTrue(self.comment().rstrip("\n").endswith(" security=n/a round=3 -->"))
        self.assertEqual(self.comment_calls(), 1)
        self.assertFalse(allow.exists())

    def test_third_round_with_blockers_posts_round_3_and_consumes_allow(self):
        self.set_pr(comments=[self.marker(1, 0, 0, 1), self.marker(1, 0, 0, 2)])
        allow = self.write_allow()
        r = self.stamp(BLOCKED)
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn("3-tur", r.stderr)
        self.assertTrue(self.comment().rstrip("\n").endswith(" blockers=1 major=0 minor=0 security=n/a round=3 -->"))
        self.assertFalse(self.stamp_file().exists())
        self.assertFalse(allow.exists())

    def test_allow_for_other_pr_or_round_or_malformed_is_not_accepted(self):
        self.set_pr(comments=[self.marker(1, 0, 0, 1), self.marker(1, 0, 0, 2)])
        for text in ("8 3 2026-11-04T10:00:00Z\n", "7 4 2026-11-04T10:00:00Z\n", "7 2 2026-11-04T10:00:00Z\n",
                     "7\n", "7 x 2026-11-04T10:00:00Z\n", "", "x\n7 3 t\n", b"\xff\xfe\x00", b"7 3\xff\n"):
            with self.subTest(allow=text):
                self.reset()
                allow = self.write_allow(text)
                self.assertLimited(self.stamp(), "--pr 7 --allow --round 3")
                self.assertEqual(allow.read_bytes(), text if isinstance(text, bytes) else text.encode())

    def test_allow_file_reads_the_first_two_fields_only(self):
        self.set_pr(comments=[self.marker(1, 0, 0, 1), self.marker(1, 0, 0, 2)])
        for text in ("7 3\n", "  7   3  junk more fields\n", "7\t3\n", "7 3 t\nsecond line\n"):
            with self.subTest(allow=text):
                self.reset()
                self.write_allow(text)
                r = self.stamp()
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(self.stamp_tail()[0], "round=3")

    def test_fourth_round_needs_its_own_allow(self):
        self.set_pr(comments=[self.marker(1, 0, 0, 1), self.marker(1, 0, 0, 2), self.marker(1, 0, 0, 3)])
        allow = self.write_allow("7 3 2026-11-04T10:00:00Z\n")  # 3-tur ruxsati 4-turga yaramaydi
        self.assertLimited(self.stamp(), "--pr 7 --allow --round 4")
        self.assertTrue(allow.exists())
        self.write_allow("7 4 2026-11-04T10:00:00Z\n")
        r = self.stamp()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.stamp_tail()[0], "round=4")
        self.assertFalse(allow.exists())

    def test_comment_failure_keeps_allow_for_retry(self):
        self.set_pr(comments=[self.marker(1, 0, 0, 1), self.marker(1, 0, 0, 2)])
        allow = self.write_allow()
        (self.gh_dir / "comment_fail").write_text("")
        r = self.stamp()
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("gh pr comment", r.stderr)
        self.assertTrue(allow.exists())
        self.assertFalse(self.stamp_file().exists())
        (self.gh_dir / "comment_fail").unlink()
        r = self.stamp()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.stamp_tail()[0], "round=3")
        self.assertFalse(allow.exists())

    def test_refusal_before_posting_keeps_allow(self):
        comments = [self.marker(1, 0, 0, 1), self.marker(1, 0, 0, 2)]
        allow = self.write_allow()
        self.set_pr(body="Boshqa matn\n", comments=comments)
        self.assertRefused(self.stamp(), f"{self.label}:")
        self.assertTrue(allow.exists())
        missing = "a" * 40
        self.set_pr(head=missing, comments=comments)
        self.assertRefused(self.stamp(head=missing), "git fetch")
        self.assertTrue(allow.exists())

    def test_posted_comments_drive_the_next_rounds(self):
        """Skript yozgan belgini o'zi qayta o'qiydi: 1-tur, 2-tur, ruxsatsiz 3-tur yo'q, `--allow` bilan 3-tur, 4-tur yana ruxsat so'raydi."""
        posted = []

        def next_head(name):
            head = commit(self.repo, name)
            self.set_pr(head=head, comments=list(posted))
            return head

        r = self.stamp()  # REPORT: muhim bor
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.stamp_tail()[0], "round=1")
        posted.append({"body": self.comment()})

        head2 = next_head("fix1.txt")
        r = self.stamp("VERDICT: blockers=0 major=0 minor=0 security=n/a\n", head=head2)
        self.assertEqual(r.returncode, 0, r.stderr)
        fields = self.stamp_file().read_text().split()
        self.assertEqual((fields[2], fields[4]), (head2, "round=2"))
        posted.append({"body": self.comment()})

        head3 = next_head("fix2.txt")
        r = self.stamp(head=head3)
        self.assertEqual(r.returncode, 4, r.stderr)
        self.assertIn("--allow --round 3", r.stderr)
        self.assertEqual(self.stamp_file().read_text().split()[2], head2)
        self.assertEqual(self.comment_calls(), 2)

        self.assertEqual(self.allow().returncode, 0)
        allow = self.allow_file()
        self.assertTrue(allow.exists())
        r = self.stamp(head=head3)
        self.assertEqual(r.returncode, 0, r.stderr)
        fields = self.stamp_file().read_text().split()
        self.assertEqual((fields[2], fields[4]), (head3, "round=3"))
        self.assertFalse(allow.exists())
        posted.append({"body": self.comment()})

        head4 = next_head("fix3.txt")
        r = self.stamp(head=head4)
        self.assertEqual(r.returncode, 4, r.stderr)
        self.assertIn("--allow --round 4", r.stderr)
        self.assertEqual(self.comment_calls(), 3)

    # --check: hech narsa yozmaydi

    def test_check_first_round_is_allowed(self):
        r = self.check()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("PR #7: 1-ko'rik ruxsat etilgan", r.stdout)
        self.assertEqual(self.gh_calls(), ["pr view 7 --json number,state,headRefOid,body,baseRefName,comments"])
        self.assertNothingWritten()

    def test_check_second_round_follows_the_first_round_result(self):
        cases = {"bloker": (self.marker(1, 0, 0, 1), True), "muhim": (self.marker(0, 1, 0, 1), True),
                 "toza": (self.marker(0, 0, 0, 1), False), "faqat mayda": (self.marker(0, 0, 4, 1), False), "toza eski belgi": (self.marker(0, 0, 0), False)}
        for name, (first, allowed) in cases.items():
            with self.subTest(name):
                self.reset()
                self.set_pr(comments=[first])
                r = self.check()
                if allowed:
                    self.assertEqual(r.returncode, 0, r.stderr)
                    self.assertIn("2-ko'rik ruxsat etilgan", r.stdout)
                    self.assertNothingWritten()
                else:
                    self.assertLimited(r, "1-ko'rigi toza")

    def test_check_third_round_needs_allow_and_does_not_consume_it(self):
        self.set_pr(comments=[self.marker(1, 0, 0, 1), self.marker(1, 0, 0, 2)])
        self.assertLimited(self.check(), "--pr 7 --allow --round 3")
        allow = self.write_allow()
        r = self.check()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("3-ko'rik ruxsat etilgan", r.stdout)
        self.assertEqual(allow.read_text(), "7 3 2026-11-04T10:00:00Z\n")
        self.assertNothingWritten()

    def test_check_keeps_existing_stamp_and_posts_nothing(self):
        old = "t 7 h 2026-01-01T00:00:00Z round=1 blockers=0 major=0 minor=0\n"
        self.stamp_file().write_text(old)
        self.write_allow()
        for comments in ([], [self.marker(1, 0, 0, 1)], [self.marker(0, 0, 0, 1)], [self.marker(1, 0, 0, 1), self.marker(1, 0, 0, 2)]):
            with self.subTest(rounds=len(comments)):
                self.set_pr(comments=comments)
                self.check()
                self.assertEqual(self.stamp_file().read_text(), old)
                self.assertIsNone(self.comment())
                self.assertEqual(self.comment_calls(), 0)
                self.assertTrue(self.allow_file().exists())

    def test_check_is_refused_for_unusable_pr(self):
        cases = {
            "yopiq": ({"state": "CLOSED"}, "ochiq emas"),
            "merge qilingan": ({"state": "MERGED"}, "ochiq emas"),
            "base main emas": ({"baseRefName": "develop"}, "base"),
            "izohlar ro'yxat emas": ({"comments": None}, "comments"),
        }
        for name, (over, text) in cases.items():
            with self.subTest(name):
                self.set_pr(**over)
                r = self.check()
                self.assertEqual(r.returncode, 1, r.stderr)
                self.assertIn(text, r.stderr)
                self.assertNothingWritten()
        (self.gh_dir / "pr.json").unlink()
        r = self.check()
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("gh pr view", r.stderr)
        self.assertNothingWritten()

    def test_check_outside_git_repo_is_refused(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        r = self.check(cwd=plain)
        self.assertEqual(r.returncode, 1)
        self.assertIn("git repo emas", r.stderr)

    def test_check_does_not_need_head_report_or_valid_sensitive_config(self):
        self.set_config({"prefix": self.prefix, "sensitive": ["("]})
        r = self.check()
        self.assertEqual(r.returncode, 0, r.stderr)


class AllowCommandTests(StampTestCase):
    """`--allow [--round K]`: foydalanuvchi "ha" degach lead ruxsat izini yozadi; izni `--check` va iz yozish o'qiydi."""

    def test_writes_allow_for_third_round_by_default(self):
        r = self.allow()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(self.allow_file().read_text(), f"^7 3 {TIME.pattern}\n$")
        self.assertIn("PR #7, 3-tur", r.stdout)
        self.assertIn(str(self.allow_file()), r.stdout)

    def test_allow_file_name_follows_the_prefix_and_leaves_nothing_else(self):
        self.assertEqual(self.allow().returncode, 0)
        self.assertEqual(self.allow_file().name, f"{self.prefix}-review-allow")
        self.assertEqual(sorted(p.name for p in self.allow_file().parent.glob("*review*")), [f"{self.prefix}-review-allow"])

    def test_round_argument(self):
        self.assertEqual(self.allow("--round", "4").returncode, 0)
        self.assertTrue(self.allow_file().read_text().startswith("7 4 "))
        self.assertEqual(self.allow("--round", "3").returncode, 0)
        self.assertTrue(self.allow_file().read_text().startswith("7 3 "))
        self.assertEqual(self.allow("--round", "12").returncode, 0)
        self.assertTrue(self.allow_file().read_text().startswith("7 12 "))

    def test_new_allow_replaces_the_previous_one(self):
        self.allow()
        self.assertEqual(self.allow("--round", "5", pr=8).returncode, 0)
        lines = self.allow_file().read_text().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("8 5 "), lines)

    def test_round_below_three_is_a_usage_error(self):
        old = self.write_allow("7 4 keep\n")
        for value in ("2", "1", "0", "-1", "x", "3.5", ""):
            with self.subTest(round=value):
                r = self.allow("--round", value)
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertEqual(old.read_text(), "7 4 keep\n")
        self.assertIn("3 yoki undan katta", self.allow("--round", "2").stderr)
        self.assertEqual(self.gh_calls(), [])

    def test_pr_must_be_a_positive_integer(self):
        for value in ("0", "-3", "x", "7.5", ""):
            with self.subTest(pr=value):
                r = self.run_script("--pr", value, "--allow")
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertFalse(self.allow_file().exists())
        r = self.run_script("--allow")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_allow_cannot_be_combined_with_stamping_or_check(self):
        for extra in (["--check"], ["--head", self.head], ["--report", str(self.report)]):
            with self.subTest(extra=extra):
                r = self.allow(*extra)
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertFalse(self.allow_file().exists())

    def test_round_without_allow_is_a_usage_error(self):
        for args in (["--pr", "7", "--check", "--round", "3"], ["--pr", "7", "--head", self.head, "--report", str(self.report), "--round", "3"]):
            with self.subTest(args=args):
                self.assertEqual(self.run_script(*args).returncode, 2)
        self.assertEqual(self.gh_calls(), [])

    def test_allow_outside_git_repo_is_refused(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        r = self.allow(cwd=plain)
        self.assertEqual(r.returncode, 1)
        self.assertIn("ruxsat izi yozilmadi", r.stderr)
        self.assertIn("git repo emas", r.stderr)
        self.assertEqual(list(plain.iterdir()), [])

    def test_allow_does_not_call_gh_or_touch_stamps(self):
        old = "t 7 h 2026-01-01T00:00:00Z round=1 blockers=0 major=0 minor=0\n"
        self.stamp_file().write_text(old)
        self.assertEqual(self.allow().returncode, 0)
        self.assertEqual(self.gh_calls(), [])
        self.assertEqual(self.stamp_file().read_text(), old)
        self.assertIsNone(self.comment())

    def test_allow_replaces_a_garbage_file_and_leaves_no_tmp(self):
        self.write_allow(b"\xff\xfe garbage")
        Path(str(self.allow_file()) + ".tmp").write_text("stale")
        self.assertEqual(self.allow().returncode, 0)
        self.assertRegex(self.allow_file().read_text(), f"^7 3 {TIME.pattern}\n$")
        self.assertFalse(Path(str(self.allow_file()) + ".tmp").exists())

    def test_written_allow_opens_exactly_its_round(self):
        self.set_pr(comments=[self.marker(1, 0, 0, 1), self.marker(1, 0, 0, 2)])
        self.assertLimited(self.stamp(), "--allow --round 3")
        self.assertEqual(self.allow().returncode, 0)
        r = self.check()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("3-ko'rik ruxsat etilgan", r.stdout)
        self.assertTrue(self.allow_file().exists())  # --check ruxsatni sarflamaydi
        r = self.stamp()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.stamp_tail()[0], "round=3")
        self.assertFalse(self.allow_file().exists())  # bir ruxsat = bir tur
        self.set_pr(comments=[self.marker(1, 0, 0, 1), self.marker(1, 0, 0, 2), self.marker(1, 0, 0, 3)])
        self.reset()
        self.assertLimited(self.stamp(), "--allow --round 4")
        self.assertEqual(self.allow("--round", "4").returncode, 0)
        r = self.stamp()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.stamp_tail()[0], "round=4")

    def test_allow_for_default_round_does_not_open_the_fourth(self):
        self.set_pr(comments=[self.marker(1, 0, 0, 1), self.marker(1, 0, 0, 2), self.marker(1, 0, 0, 3)])
        self.allow()
        self.assertLimited(self.stamp(), "--allow --round 4")
        self.assertTrue(self.allow_file().exists())

    def test_allow_for_another_pr_does_not_open_this_one(self):
        self.set_pr(comments=[self.marker(1, 0, 0, 1), self.marker(1, 0, 0, 2)])
        self.assertEqual(self.allow(pr=8).returncode, 0)
        self.assertLimited(self.stamp(), "--allow --round 3")

    def test_allow_cannot_open_rounds_one_and_two(self):
        """Ruxsat izi 1-2-turlarni ochmaydi: 2-tur toza 1-turdan keyin yopiq qoladi (qo'lda yozilgan "7 2" ham)."""
        self.set_pr(comments=[self.marker(0, 0, 0, 1)])
        self.write_allow("7 2 t\n")
        self.assertLimited(self.stamp(), "1-ko'rigi toza")
        self.assertTrue(self.allow_file().exists())


class PerPrStampTests(StampTestCase):
    """Iz PR bo'yicha (`<prefix>-review-ok-<PR>`): boshqa PR ning ko'rigi bu PR izini o'chirmaydi va ustidan yozmaydi."""

    def setUp(self):
        super().setUp()
        self.head8 = commit(self.repo, "other.txt")  # PR 8: shu branchning keyingi commiti
        self.set_pr(head=self.head8, number=8)

    def test_stamp_is_named_after_the_pr_and_no_shared_file_is_written(self):
        r = self.stamp(CLEAN, pr=7)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.stamp_names(), [f"{self.prefix}-review-ok-7"])
        r = self.stamp(CLEAN, pr=8, head=self.head8)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.stamp_names(), [f"{self.prefix}-review-ok-7", f"{self.prefix}-review-ok-8"])
        self.assertFalse(self.legacy_stamp_file().exists())

    def test_other_prs_blockers_keep_a_clean_prs_stamp(self):
        """Ko'rikchi stsenariysi: PR 7 toza 1-tur, keyin PR 8 blokerli ko'rik, keyin PR 8 toza: ikkala iz o'z PR raqami bilan turadi."""
        self.assertEqual(self.stamp(CLEAN, pr=7).returncode, 0)
        stamp7 = self.stamp_file(pr=7).read_text()
        self.assertEqual(self.stamp_names(), [f"{self.prefix}-review-ok-7"])

        r = self.stamp(BLOCKED, pr=8, head=self.head8)  # PR 8 ning 1-turi: bloker
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertEqual(self.stamp_file(pr=7).read_text(), stamp7)
        self.assertEqual(self.stamp_names(), [f"{self.prefix}-review-ok-7"])

        self.set_pr(head=self.head8, number=8, comments=[{"body": self.comment()}])  # skript yozgan belgi: PR 8 ning 1-turi o'tgan
        r = self.stamp(CLEAN, pr=8, head=self.head8)  # 2-tur ruxsat etilgan: 1-turda bloker bor edi
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.stamp_names(), [f"{self.prefix}-review-ok-7", f"{self.prefix}-review-ok-8"])
        self.assertEqual(self.stamp_file(pr=7).read_text(), stamp7)
        f7, f8 = (self.stamp_file(pr=n).read_text().split() for n in (7, 8))
        self.assertEqual((f7[1], f7[2], f7[4]), ("7", self.head, "round=1"))
        self.assertEqual((f8[1], f8[2], f8[4]), ("8", self.head8, "round=2"))
        self.assertFalse(self.legacy_stamp_file().exists())

    def test_allow_of_one_pr_is_not_consumed_by_another_prs_round(self):
        self.set_pr(head=self.head8, number=8, comments=[self.marker(1, 0, 0, 1), self.marker(1, 0, 0, 2)])
        self.write_allow("7 3 t\n")
        self.assertEqual(self.stamp(CLEAN, pr=8, head=self.head8).returncode, 4)
        self.assertTrue(self.allow_file().exists())


class SensitiveTests(StampTestCase):
    """`sensitive` regex ro'yxati config.json dan: o'zgargan yo'llardan biriga mos kelsa security=done shart."""

    PATHS = ["src/auth/login.py", "db/migrations/001_init.sql", "migrations/002.sql", "config/secrets.yml", "config/session.yml",
             "keys/server.pem", ".claude/settings.json"]
    HARMLESS = ["src/authz.py", "src/auth.py", "docs/migrations.md", "src/app/migrationsx/a.py", "config/secrets.yml.example", "config/other.yml",
                "keys/server.pem.bak", "docs/.claude/x.md", "README.md", "web/app.ts", "src/auth_helpers/a.py"]

    def test_every_listed_sensitive_path_needs_security_done(self):
        for path in self.PATHS:
            with self.subTest(path=path):
                self.branch_with(path)
                self.reset()
                r = self.stamp(REPORT)
                self.assertRefused(r, path)
                self.assertIn("security=done", r.stderr)
                r = self.stamp(DONE)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn("security=done round=1 -->", self.comment())

    def test_harmless_paths_do_not_need_security(self):
        self.branch_with(*self.HARMLESS)
        r = self.stamp()
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_each_regex_of_the_list_is_used(self):
        """Ro'yxatning har bir elementi alohida ishlaydi: faqat shu element mos keladigan yo'l bilan."""
        only = {r"^src/auth/": "src/auth/x.py", r"(?:^|/)migrations/": "a/migrations/x.sql", r"\.pem$": "k/server.pem"}
        for pattern, path in only.items():
            with self.subTest(pattern=pattern):
                self.set_config({"prefix": self.prefix, "source_labels": LABELS, "sensitive": [pattern]})
                self.branch_with(path, "web/app.ts")
                self.reset()
                self.assertRefused(self.stamp(REPORT), path)
                self.branch_with("src/other.py", "web/app.ts")
                self.assertEqual(self.stamp(REPORT).returncode, 0)

    def test_all_sensitive_paths_are_named(self):
        self.branch_with(self.PATHS[0], self.PATHS[3], "web/x.ts")
        r = self.stamp()
        self.assertIn(self.PATHS[0], r.stderr)
        self.assertIn(self.PATHS[3], r.stderr)
        self.assertNotIn("web/x.ts", r.stderr)

    def test_long_list_is_summarised(self):
        self.branch_with(*[f"src/auth/f{i:02d}.py" for i in range(23)])
        r = self.stamp()
        self.assertRefused(r, "src/auth/f00.py")
        self.assertIn("va yana 3 ta", r.stderr)
        self.assertNotIn("src/auth/f22.py", r.stderr)

    def test_blockers_on_sensitive_diff_still_remove_stamp(self):
        self.branch_with(self.PATHS[0])
        self.stamp_file().write_text("old 7 old 2026-01-01T00:00:00Z\n")
        r = self.stamp(BLOCKED)
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertFalse(self.stamp_file().exists())

    def test_deleted_sensitive_file_counts(self):
        self.branch_with("src/auth/login.py")
        git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")  # origin/main endi sezgir fayl bilan
        git(self.repo, "rm", "-q", "src/auth/login.py")
        git(self.repo, "commit", "-q", "-m", "rm")
        self.head = git(self.repo, "rev-parse", "HEAD")
        self.set_pr()
        self.assertRefused(self.stamp(), "src/auth/login.py")

    def test_renamed_sensitive_file_counts_by_both_names(self):
        self.branch_with(base_files=["src/auth/old.py"])
        (self.repo / "web").mkdir()
        git(self.repo, "mv", "src/auth/old.py", "web/old.py")
        git(self.repo, "commit", "-q", "-m", "mv")
        self.head = git(self.repo, "rev-parse", "HEAD")
        self.set_pr()
        self.assertRefused(self.stamp(), "src/auth/old.py")

    def test_unchanged_sensitive_files_of_the_base_do_not_count(self):
        self.branch_with("web/new.ts", base_files=["src/auth/old.py", "keys/server.pem"])
        self.assertEqual(self.stamp().returncode, 0)

    def test_diff_is_taken_from_the_merge_base_not_from_the_base_tip(self):
        """Base ning o'zi oldinga ketgan bo'lsa ham (undagi sezgir fayl) PR ning o'zgarishi faqat merge-base dan boshlanadi."""
        git(self.repo, "switch", "-q", "main")
        commit(self.repo, "src/auth/newer.py")
        git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        git(self.repo, "switch", "-q", "feature")
        self.assertEqual(self.stamp().returncode, 0)

    def test_security_done_is_required_exactly_when_sensitive(self):
        self.branch_with("src/auth/login.py")
        for report in (REPORT, CLEAN, "VERDICT: blockers=0 major=0 minor=0\n"):
            with self.subTest(report=report.splitlines()[-1]):
                self.assertRefused(self.stamp(report), "security=done")

    def test_empty_sensitive_list_means_nothing_is_sensitive(self):
        self.set_config({"prefix": self.prefix, "source_labels": LABELS, "sensitive": []})
        self.branch_with(".claude/settings.json", "src/auth/login.py")
        self.assertEqual(self.stamp(REPORT).returncode, 0)

    def test_invalid_regex_in_config_is_a_refusal_naming_the_key(self):
        self.set_config({"prefix": self.prefix, "source_labels": LABELS, "sensitive": [r"^ok/", "(unclosed"]})
        self.branch_with("web/app.ts")
        r = self.stamp()
        self.assertRefused(r, "sensitive[1]")
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(self.stamp(BLOCKED).returncode, 3)  # blokerli hisobot regex ga tayanmaydi
        self.assertEqual(self.check().returncode, 0)

    def test_non_string_sensitive_entries_fall_back_to_the_defaults_with_a_warning(self):
        self.set_config({"prefix": self.prefix, "source_labels": LABELS, "sensitive": [r"^src/", 5]})
        self.branch_with(".claude/settings.json")
        r = self.stamp()
        self.assertRefused(r, ".claude/settings.json")
        self.assertIn("sensitive", r.stderr.splitlines()[0])
        self.assertTrue(r.stderr.startswith("ogohlantirish:"), r.stderr)


class DefaultSensitiveTests(StampTestCase):
    """Sozlamasiz sezgir yo'llar ro'yxati generik: .claude/, .githooks/, .github/workflows/ (ildizda)."""
    CONFIG = None
    prefix = "quality"
    label = "Source"

    def test_default_sensitive_list_without_config(self):
        for path in (".claude/settings.json", ".claude/hooks/guard.py", ".githooks/pre-push", ".github/workflows/ci.yml"):
            with self.subTest(path=path):
                self.branch_with(path)
                self.reset()
                self.assertRefused(self.stamp(REPORT), path)
                self.assertEqual(self.stamp(DONE).returncode, 0)

    def test_default_list_leaves_other_paths_alone(self):
        self.branch_with(".github/ISSUE_TEMPLATE/bug.md", "docs/.claude/x.md", "src/auth/login.py", "migrations/1.sql", "README.md", ".claudex/y")
        self.assertEqual(self.stamp(REPORT).returncode, 0)


class SourceLabelTests(StampTestCase):
    """`source_labels` config dan: PR matnida `<yorliq>: <matn>` qatori (ro'yxatdan istalgani, katta-kichik harfga befarq)."""

    FIRST, SECOND = LABELS

    def accepted(self, body):
        self.reset()
        self.set_pr(body=body)
        r = self.stamp()
        self.assertEqual(r.returncode, 0, f"{body!r}: {r.stderr}")

    def rejected(self, body, text):
        self.reset()
        self.set_pr(body=body)
        self.assertRefused(self.stamp(), text)

    def test_any_label_of_the_list_and_any_letter_case(self):
        F, S = self.FIRST, self.SECOND
        for body in (f"{F}: x\n", f"{S}: x\n", f"{S.lower()}: x\n", f"{F.upper()}: x\n", f"- {S}: x\n", f"**{F}**: x\n"):
            with self.subTest(body=body):
                self.accepted(body)

    def test_unlisted_labels_and_lookalikes_are_rejected(self):
        F, S = self.FIRST, self.SECOND
        for body in ("Origin: x\n", f"{F}a: x\n", f"{S}s: x\n", f"See {S}: x\n", f"{F} x\n", f"{F};x\n"):
            with self.subTest(body=body):
                self.rejected(body, f"{F}:")

    def test_message_lists_every_label(self):
        self.set_pr(body="x\n")
        r = self.stamp()
        self.assertRefused(r, f"`{self.FIRST}: <matn>`")
        self.assertIn(f"`{self.SECOND}: <matn>`", r.stderr)

    def test_single_label_config(self):
        F, S = self.FIRST, self.SECOND
        self.set_config({"prefix": self.prefix, "source_labels": [F]})
        self.accepted(f"{F}: x\n")
        self.rejected(f"{S}: x\n", f"{F}:")
        self.assertNotIn(S, self.stamp().stderr)

    def test_default_label_is_source_only(self):
        self.set_config(None)
        self.accepted("Source: x\n")
        self.accepted("- **source:** x\n")
        self.rejected(f"{self.FIRST}: x\n", "Source:")

    def test_labels_are_literal_text_not_regex(self):
        self.set_config({"prefix": self.prefix, "source_labels": ["Ref (RFC)", "C++", "a.b"]})
        for body in ("Ref (RFC): 12\n", "C++: 3\n", "a.b: 1\n"):
            with self.subTest(body=body):
                self.accepted(body)
        for body in ("Ref RFC: 12\n", "C: 3\n", "aXb: 1\n", "Ref (RFC)x: 1\n"):
            with self.subTest(body=body):
                self.rejected(body, "Ref (RFC):")

    def test_non_ascii_label(self):
        self.set_config({"prefix": self.prefix, "source_labels": ["Источник"]})
        self.accepted("источник: ТЗ 4.2\n")
        self.accepted("**Источник:** ТЗ 4.2\n")
        self.rejected("Source: x\n", "Источник:")

    def test_label_with_spaces(self):
        self.set_config({"prefix": self.prefix, "source_labels": ["Linked issue"]})
        self.accepted("Linked issue: #12\n")
        self.rejected("Linked: #12\n", "Linked issue:")


class ConfigTests(StampTestCase):
    """config.json: `--config` > $QUALITY_CONFIG > <skript papkasi>/config.json; yo'q = standart, buzuq = standart + bitta ogohlantirish."""

    CONFIG = None  # har test o'z sozlamasini yozadi; boshida fayl yo'q = standart nomlar
    prefix = "quality"
    label = "Source"

    def defaults_work(self, r):
        """Standart nomlar bilan muvaffaqiyat: quality-review-ok-7, belgi `quality-review`."""
        self.assertEqual(r.returncode, 0, r.stderr)
        stamp = self.git_file("quality-review-ok-7")
        self.assertTrue(stamp.exists())
        self.assertIn("<!-- quality-review head=", self.comment())
        self.reset_defaults()

    def reset_defaults(self):
        for p in (self.git_file("quality-review-ok-7"), self.gh_dir / "comment.md"):
            p.unlink(missing_ok=True)

    def install_copy(self, name="inst"):
        """Skriptning nusxasi <tmp>/<name>/quality/review_stamp.py (o'rnatilgan joylashuv): yoniga config.json qo'yish mumkin."""
        d = self.tmp / name / "quality"
        d.mkdir(parents=True)
        shutil.copy(SCRIPT, d / "review_stamp.py")
        return d

    def run_copy(self, d, *args, env=None):
        return subprocess.run([sys.executable, str(d / "review_stamp.py"), "--pr", "7", *args], cwd=str(self.repo), capture_output=True, text=True,
                              env=env or {k: v for k, v in self.env.items() if k != "QUALITY_CONFIG"})

    def test_missing_config_file_gives_defaults_silently(self):
        r = self.stamp()  # QUALITY_CONFIG mavjud bo'lmagan faylga yo'naltirilgan
        self.assertEqual(r.stderr, "")
        self.defaults_work(r)

    def test_script_dir_config_json_is_used_without_env(self):
        d = self.install_copy()
        (d / "config.json").write_text(json.dumps({"prefix": "fromdir"}))
        r = self.run_copy(d, "--head", self.head, "--report", str(self.report))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.git_file("fromdir-review-ok-7").exists())
        self.assertIn("<!-- fromdir-review head=", self.comment())

    def test_no_config_next_to_the_script_gives_defaults(self):
        d = self.install_copy()
        r = self.run_copy(d, "--head", self.head, "--report", str(self.report))
        self.assertEqual(r.stderr, "")
        self.defaults_work(r)

    def test_env_beats_script_dir_and_argument_beats_env(self):
        d = self.install_copy()
        (d / "config.json").write_text(json.dumps({"prefix": "fromdir"}))
        by_env = self.set_config({"prefix": "fromenv"}, "env.json")
        by_arg = self.tmp / "arg.json"
        by_arg.write_text(json.dumps({"prefix": "fromarg"}))
        r = self.run_copy(d, "--head", self.head, "--report", str(self.report), env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.git_file("fromenv-review-ok-7").exists())
        self.assertFalse(self.git_file("fromdir-review-ok-7").exists())
        self.assertTrue(by_env.exists())
        r = self.run_copy(d, "--config", str(by_arg), "--head", self.head, "--report", str(self.report), env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.git_file("fromarg-review-ok-7").exists())

    def test_config_argument_works_in_every_mode(self):
        cfg = self.set_config({"prefix": "acme"})
        self.env.pop("QUALITY_CONFIG")
        self.assertEqual(self.run_script("--pr", "7", "--allow", "--config", str(cfg)).returncode, 0)
        self.assertTrue(self.git_file("acme-review-allow").exists())
        r = self.run_script("--pr", "7", "--check", "--config", str(cfg))
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.run_script("--pr", "7", "--head", self.head, "--report", str(self.report), "--config", str(cfg))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.stamp_names(), [])  # standart nom yozilmagan
        self.assertTrue(self.git_file("acme-review-ok-7").exists())

    def test_prefix_names_everything(self):
        self.set_config({"prefix": "acme"})
        self.set_pr(comments=[self.marker(1, 0, 0, 1), self.marker(1, 0, 0, 2)])  # standart prefiks belgilari: acme uchun tur emas
        self.assertEqual(self.stamp().returncode, 0)
        self.assertEqual(self.stamp_tail_of("acme")[0], "round=1")
        self.assertIn("<!-- acme-review head=", self.comment())
        self.assertEqual(self.allow().returncode, 0)
        names = sorted(p.name for p in self.git_file("x").parent.glob("*-review-*"))
        self.assertEqual(names, ["acme-review-allow", "acme-review-ok-7"])

    def stamp_tail_of(self, prefix):
        return self.git_file(f"{prefix}-review-ok-7").read_text().split()[4:]

    def test_gate_command_appears_in_the_clean_round_message(self):
        self.set_config({"prefix": self.prefix, "gate_command": "just verify --all"})
        self.set_pr(comments=[self.marker(0, 0, 0, 1)])
        r = self.stamp()
        self.assertLimited(r, "`just verify --all`")
        self.assertNotIn("make ci", r.stderr)
        self.set_config({"prefix": self.prefix})
        self.assertLimited(self.stamp(), "`make ci`")

    def test_base_branch_is_the_first_protected_branch_and_ref_defaults_to_origin_of_it(self):
        self.set_config({"protected": ["trunk", "main"]})
        shutil.rmtree(self.repo)
        self.head = self.make_branch(self.repo, "change.txt", base="trunk")
        self.set_pr(baseRefName="trunk")
        r = self.stamp()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.reset_defaults()
        self.set_pr(baseRefName="main")
        r = self.stamp()
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("base i trunk emas (main)", r.stderr)
        git(self.repo, "update-ref", "-d", "refs/remotes/origin/trunk")
        self.set_pr(baseRefName="trunk")
        self.assertIn("git fetch origin trunk", self.stamp().stderr)

    def test_ref_from_config_is_the_diff_base_and_names_the_fetch_hint(self):
        self.set_config({"protected": ["develop"], "ref": "upstream/develop", "sensitive": [r"^src/auth/"]})
        shutil.rmtree(self.repo)
        self.head = self.make_branch(self.repo, "src/auth/x.py", base="develop", remote="upstream")
        self.set_pr(baseRefName="develop")
        self.assertRefused(self.stamp(REPORT), "src/auth/x.py")
        git(self.repo, "update-ref", "-d", "refs/remotes/upstream/develop")
        r = self.stamp(REPORT)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("git fetch upstream develop", r.stderr)

    def test_other_keys_in_the_file_are_ignored(self):
        self.set_config({"baseline": "abc", "merge_style": "merge", "quality_log": {"file": "x.md"}, "future_key": [1, 2]})
        r = self.stamp()
        self.assertEqual(r.stderr, "")
        self.defaults_work(r)

    def test_null_values_mean_unset_without_a_warning(self):
        self.set_config({"prefix": None, "ref": None, "source_labels": None, "sensitive": None, "protected": None, "gate_command": None})
        r = self.stamp()
        self.assertEqual(r.stderr, "")
        self.defaults_work(r)

    def test_unreadable_config_gives_defaults_and_one_warning_line(self):
        directory = self.tmp / "a-directory"
        directory.mkdir()
        cases = {"buzuq JSON": "{not json", "bo'sh fayl": "", "ikkilik": b"\xff\xfe\x00{", "ro'yxat": "[]", "null": "null",
                 "juda chuqur JSON": "[" * 100000, "papka": directory}
        for name, content in cases.items():
            with self.subTest(name):
                if isinstance(content, Path):
                    self.env["QUALITY_CONFIG"] = str(content)
                else:
                    path = self.tmp / "bad.json"
                    path.write_bytes(content if isinstance(content, bytes) else content.encode())
                    self.env["QUALITY_CONFIG"] = str(path)
                r = self.stamp()
                lines = [ln for ln in r.stderr.splitlines() if ln.strip()]
                self.assertEqual(len(lines), 1, r.stderr)
                self.assertTrue(lines[0].startswith("ogohlantirish:"), lines)
                self.assertIn("standart", lines[0])
                self.assertNotIn("Traceback", r.stderr)
                self.defaults_work(r)

    def test_unreadable_config_does_not_break_check_and_allow(self):
        self.env["QUALITY_CONFIG"] = str(self.tmp)
        r = self.check()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len([ln for ln in r.stderr.splitlines() if ln.strip()]), 1)
        r = self.allow()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.git_file("quality-review-allow").exists())

    def test_wrongly_typed_keys_fall_back_per_key_with_a_warning_each(self):
        bad = {"prefix": 5, "gate_command": "", "source_labels": "Source", "sensitive": "x", "protected": [], "ref": 3}
        self.set_config(bad)
        r = self.stamp()
        warned = sorted(re.search(r"`(\w+)`", ln).group(1) for ln in r.stderr.splitlines() if ln.startswith("ogohlantirish:"))
        self.assertEqual(warned, sorted(bad))
        self.defaults_work(r)

    def test_good_keys_survive_a_bad_neighbour(self):
        self.set_config({"prefix": "acme", "source_labels": 5, "gate_command": "just ci"})
        r = self.stamp()
        self.assertEqual(r.returncode, 0, r.stderr)  # yorliq standart (Source), prefiks acme
        self.assertEqual(len(r.stderr.splitlines()), 1)
        self.assertIn("source_labels", r.stderr)
        self.assertTrue(self.git_file("acme-review-ok-7").exists())

    def test_invalid_prefixes_fall_back_to_the_default(self):
        for prefix in ("a/b", "../x", "-x", "", "a b"):
            with self.subTest(prefix=prefix):
                self.set_config({"prefix": prefix})
                r = self.stamp()
                self.defaults_work(r)
                self.assertIn("`prefix`", r.stderr)

    def test_valid_prefix_shapes(self):
        for prefix in ("my-proj", "proj.v2", "9lives"):
            with self.subTest(prefix=prefix):
                self.set_config({"prefix": prefix})
                self.reset_defaults()
                r = self.stamp()
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(r.stderr, "")
                self.assertTrue(self.git_file(f"{prefix}-review-ok-7").exists())
                self.assertIn(f"<!-- {prefix}-review head=", self.comment())

    def test_prefix_with_regex_characters_counts_only_its_own_markers(self):
        self.set_config({"prefix": "a.b"})
        own = {"body": f"<!-- a.b-review head={MARKER_HEAD} tree={MARKER_TREE} blockers=1 major=0 minor=0 security=n/a round=1 -->"}
        lookalike = {"body": f"<!-- aXb-review head={MARKER_HEAD} tree={MARKER_TREE} blockers=1 major=0 minor=0 security=n/a round=1 -->"}
        self.set_pr(comments=[lookalike])
        self.assertEqual(self.stamp().returncode, 0)
        self.assertEqual(self.stamp_tail_of("a.b")[0], "round=1")
        self.set_pr(comments=[own])
        self.assertEqual(self.stamp().returncode, 0)
        self.assertEqual(self.stamp_tail_of("a.b")[0], "round=2")


class ProvenFormatTests(StampTestCase):
    """Misol sozlamadagi prefix isbotlangan loyihaning iz nomlari, izoh belgisi va iz satrini aynan takrorlaydi (merge hooki shularni o'qiydi)."""

    def test_exact_names_marker_and_stamp_line(self):
        r = self.stamp()
        self.assertEqual(r.returncode, 0, r.stderr)
        tree = git(self.repo, "rev-parse", f"{self.head}^{{tree}}")
        git_dir = self.stamp_file().parent
        self.assertEqual(sorted(p.name for p in git_dir.glob("sos-*")), ["sos-review-ok-7"])
        self.assertEqual(git(self.repo, "rev-parse", "--git-path", "sos-review-ok-7"), ".git/sos-review-ok-7")
        self.assertRegex(
            (git_dir / "sos-review-ok-7").read_text(),
            f"^{tree} 7 {self.head} {TIME.pattern} round=1 blockers=0 major=1 minor=2\n$")
        self.assertEqual(
            self.comment(),
            "# Ko'rik\n\nHammasi joyida.\n\nVERDICT: blockers=0 major=1 minor=2 security=n/a\n\n"
            f"<!-- sos-review head={self.head} tree={tree} blockers=0 major=1 minor=2 security=n/a round=1 -->\n")

    def test_exact_allow_file_name_and_line(self):
        self.assertEqual(self.allow("--round", "4").returncode, 0)
        git_dir = self.stamp_file().parent
        self.assertRegex((git_dir / "sos-review-allow").read_text(), f"^7 4 {TIME.pattern}\n$")

    def test_old_markers_without_round_are_rounds_and_new_ones_with_round(self):
        head = f"<!-- {PREFIX}-review head={MARKER_HEAD} tree={MARKER_TREE}"
        legacy = {"body": f"{head} blockers=1 major=0 minor=0 security=done -->"}
        current = {"body": f"{head} blockers=0 major=1 minor=0 security=n/a round=2 -->"}
        self.set_pr(comments=[legacy, current])
        self.assertLimited(self.stamp(), "1-tur bloker 1 muhim 0 mayda 0", "2-tur bloker 0 muhim 1 mayda 0")

    def test_defaults_do_not_leak_the_proven_names(self):
        """Sozlamasiz skript misol sozlamadagi nomlarni ishlatmaydi."""
        self.set_config(None)
        self.set_pr(body="Source: x\n")
        self.assertEqual(self.stamp().returncode, 0)
        self.assertFalse(list(self.stamp_file().parent.glob(f"{PREFIX}-*")))
        self.assertNotIn(f"{PREFIX}-review", self.comment())


class SubmoduleTests(StampTestCase):
    def add_submodule(self, path="libs/shared"):
        sub_src = self.tmp / "sub-src"
        git(self.tmp, "init", "-q", "-b", "main", str(sub_src))
        commit(sub_src, "00.md")
        git(self.repo, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(sub_src), path)
        sub = self.repo / path
        git(sub, "switch", "-q", "-c", "docs-fix")
        return sub

    def test_runs_from_second_repo_and_writes_into_its_git_dir(self):
        """Submodule: o'sha papkaga `cd` qilib chaqirilsa skript o'sha repo ning git papkasiga yozadi."""
        sub = self.add_submodule()
        head = commit(sub, "01.md")
        self.set_pr(head=head)
        r = self.stamp(cwd=sub, head=head)
        self.assertEqual(r.returncode, 0, r.stderr)
        sub_stamp = self.stamp_file(sub)
        self.assertNotEqual(sub_stamp, self.stamp_file(self.repo))
        self.assertIn("modules", str(sub_stamp))
        self.assertEqual(sub_stamp.read_text().split()[:3], [git(sub, "rev-parse", f"{head}^{{tree}}"), "7", head])
        self.assertEqual(self.stamp_names(sub), [f"{self.prefix}-review-ok-7"])
        self.assertFalse(self.stamp_file(self.repo).exists())
        # loyiha repo ning feature head i submodule da yo'q: o'sha repo uchun rad etiladi
        r = self.stamp(cwd=sub, head=self.head)
        self.assertEqual(r.returncode, 1)

    def test_allow_is_read_from_the_submodule_git_dir(self):
        """Ruxsat izi ham skript ishga tushgan repo ning git papkasida: loyiha repo dagisi submodule PR i uchun hisoblanmaydi."""
        sub = self.add_submodule()
        head = commit(sub, "01.md")
        self.set_pr(head=head, comments=[self.marker(1, 0, 0, 1), self.marker(1, 0, 0, 2)])
        main_allow = self.write_allow()
        r = self.stamp(cwd=sub, head=head)
        self.assertEqual(r.returncode, 4, r.stderr)
        sub_allow = self.write_allow(repo=sub)
        self.assertNotEqual(sub_allow, main_allow)
        r = self.stamp(cwd=sub, head=head)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.stamp_file(sub).read_text().split()[4], "round=3")
        self.assertFalse(sub_allow.exists())
        self.assertTrue(main_allow.exists())

    def test_allow_command_writes_into_the_repo_of_the_current_directory(self):
        sub = self.add_submodule()
        self.assertEqual(self.allow(cwd=sub).returncode, 0)
        self.assertTrue(self.allow_file(sub).exists())
        self.assertFalse(self.allow_file(self.repo).exists())
        self.assertEqual(self.allow("--round", "4", cwd=self.repo).returncode, 0)
        self.assertTrue(self.allow_file(self.repo).read_text().startswith("7 4 "))
        self.assertTrue(self.allow_file(sub).read_text().startswith("7 3 "))

    def test_hint_command_is_usable_from_the_submodule_dir(self):
        sub = self.add_submodule()
        head = commit(sub, "01.md")
        self.set_pr(head=head, comments=[self.marker(1, 0, 0, 1), self.marker(1, 0, 0, 2)])
        r = self.stamp(cwd=sub, head=head)
        m = re.search(r"`python3 (\S+) --pr 7 --allow --round 3`", r.stderr)
        self.assertIsNotNone(m, r.stderr)
        self.assertEqual((sub / m.group(1)).resolve(), SCRIPT.resolve())

    def test_sensitive_diff_is_taken_in_the_submodule_repo(self):
        sub = self.add_submodule()
        git(sub, "update-ref", "refs/remotes/origin/main", "main")
        head = commit(sub, "src/auth/x.py")
        self.set_pr(head=head)
        r = self.stamp(REPORT, cwd=sub, head=head)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("src/auth/x.py", r.stderr)

    def test_not_a_git_repo(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        r = self.stamp(cwd=plain)
        self.assertEqual(r.returncode, 1)
        self.assertIn("git repo emas", r.stderr)


class DefaultsTests(StampTestCase):
    """Sozlamasiz (config.json yo'q) ishlash: prefix quality, yorliq Source, base main: iz nomlari va belgi generik."""
    CONFIG = None
    prefix = "quality"
    label = "Source"

    def test_stamp_marker_and_comment_use_the_generic_names(self):
        r = self.stamp()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr, "")
        tree = git(self.repo, "rev-parse", f"{self.head}^{{tree}}")
        git_dir = self.stamp_file().parent
        self.assertEqual(sorted(p.name for p in git_dir.glob("*-review-*")), ["quality-review-ok-7"])
        self.assertRegex((git_dir / "quality-review-ok-7").read_text(), f"^{tree} 7 {self.head} {TIME.pattern} round=1 blockers=0 major=1 minor=2\n$")
        self.assertEqual(self.comment().rstrip("\n").splitlines()[-1],
                         f"<!-- quality-review head={self.head} tree={tree} blockers=0 major=1 minor=2 security=n/a round=1 -->")

    def test_rounds_follow_the_generic_marker(self):
        self.set_pr(comments=[self.marker(0, 0, 0, 1)])
        self.assertLimited(self.stamp(), "1-ko'rigi toza", "`make ci`")
        self.set_pr(comments=[self.marker(1, 0, 0, 1)])
        r = self.stamp()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.stamp_tail()[0], "round=2")
        self.set_pr(comments=[{"body": self.marker(1, 0, 0, 1)["body"].replace("quality-review", f"{PREFIX}-review")}])  # boshqa loyiha belgisi tur emas
        self.reset()
        self.assertEqual(self.stamp().returncode, 0)
        self.assertEqual(self.stamp_tail()[0], "round=1")

    def test_third_round_only_with_the_allow_file_written_by_the_allow_command(self):
        self.set_pr(comments=[self.marker(1, 0, 0, 1), self.marker(1, 0, 0, 2)])
        self.assertLimited(self.stamp(), "--pr 7 --allow --round 3")
        self.assertEqual(self.allow().returncode, 0)
        self.assertEqual(self.allow_file().name, "quality-review-allow")
        r = self.stamp()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.stamp_tail()[0], "round=3")
        self.assertFalse(self.allow_file().exists())

    def test_check_writes_nothing(self):
        self.write_allow("7 3 t\n")
        self.set_pr(comments=[self.marker(1, 0, 0, 1), self.marker(1, 0, 0, 2)])
        r = self.check()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("3-ko'rik ruxsat etilgan", r.stdout)
        self.assertNothingWritten()
        self.assertTrue(self.allow_file().exists())

    def test_blockers_remove_only_this_prs_stamp(self):
        other = "t 8 h 2026-01-01T00:00:00Z round=1 blockers=0 major=0 minor=0\n"
        self.stamp_file(pr=8).write_text(other)
        self.assertEqual(self.stamp().returncode, 0)
        self.assertEqual(self.stamp(BLOCKED).returncode, 3)
        self.assertEqual(self.stamp_names(), ["quality-review-ok-8"])
        self.assertEqual(self.stamp_file(pr=8).read_text(), other)

    def test_base_branch_is_main(self):
        self.set_pr(baseRefName="master")
        self.assertRefused(self.stamp(), "base i main emas (master)")


if __name__ == "__main__":
    unittest.main()
