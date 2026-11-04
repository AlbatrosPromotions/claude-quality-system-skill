#!/usr/bin/env python3
"""merge_guard.py (PreToolUse hook) testlari: `gh pr merge` darvozasi, ko'rik hukmi va ajdod qoidasi, nishon tahlili, cd/submodule,
ikkinchi qoida (iz skriptlarining yozish shakllari: ko'rik izini faqat reviewer yozadi, darvoza izini qo'lda yozib bo'lmaydi), sozlama (iz nomlari, xabarlar), tezlik.

Qora quti: hook stdin JSON ({tool_name, tool_input.command, cwd, agent_type}) bilan ishga tushadi, gh o'rniga PATH boshidagi soxta skript
(PR ma'lumoti cwd papka nomi bo'yicha: <FAKE_GH_DIR>/<papka>.json), git repo lar vaqtinchalik papkada, sozlama QUALITY_CONFIG orqali
(haqiqiy loyiha sozlamasiga tegilmaydi). Ikki joylashuvni biladi: to'plam papkasi (merge_guard.py yonma-yon) va o'rnatilgan loyiha
(.claude/hooks/merge-guard.py, .claude/quality/tests).

  python3 -m unittest discover -s .claude/quality/tests -p 'test_merge_guard.py' -v
"""
import itertools
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

QUALITY = Path(__file__).resolve().parent.parent  # to'plam papkasi yoki <loyiha>/.claude/quality
if (QUALITY / "merge_guard.py").is_file():
    HOOK = QUALITY / "merge_guard.py"
else:
    HOOK = QUALITY.parent / "hooks" / "merge-guard.py"
GATE_STAMP = QUALITY / "gate_stamp.py"

HOME = tempfile.mkdtemp(prefix="merge-guard-home-")
BASE_ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_") and k not in ("CLAUDE_PROJECT_DIR", "QUALITY_CONFIG")}
BASE_ENV.update(
    HOME=HOME, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1",
    GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com",
)

# Soxta gh: `pr view ...` joriy papka nomiga mos PR faylini qaytaradi (yo'q bo'lsa xato), argumentlar calls.log ga yoziladi.
# Raqamsiz (joriy branch PR i): <papka>.json; `pr view N`: <papka>.N.json, yo'q bo'lsa <papka>.json faqat raqami N bo'lsa
FAKE_GH = """#!/usr/bin/env python3
import json, os, sys
d = os.environ["FAKE_GH_DIR"]
with open(os.environ.get("FAKE_GH_LOG") or os.path.join(d, "calls.log"), "a") as f:
    f.write(os.path.basename(os.getcwd()) + ": " + " ".join(sys.argv[1:]) + "\\n")
if sys.argv[1:3] == ["pr", "view"]:
    base = os.path.join(d, os.path.basename(os.getcwd()))
    num = sys.argv[3] if len(sys.argv) > 3 and not sys.argv[3].startswith("-") else None
    p = base + ("." + num if num else "") + ".json"
    if num and not os.path.exists(p) and os.path.exists(base + ".json") and str(json.load(open(base + ".json"))["number"]) == num:
        p = base + ".json"
    if not os.path.exists(p):
        sys.stderr.write("no pull requests found\\n")
        sys.exit(1)
    sys.stdout.write(open(p).read())
else:
    sys.exit(64)
"""

WORKERS = max(2, min(8, os.cpu_count() or 2))  # uzun buyruq ro'yxatlari parallel yuritiladi (har yurish o'z gh jurnali bilan)
SEQ = itertools.count()

# sozlama misollari: sos formati iz nomlarini baytma-bayt takrorlaydi (sos-ci-ok, sos-review-ok-7)
SOS = {"prefix": "sos", "gate_command": "make ci", "reviewer": "opus-reviewer"}
MERGE = "gh pr merge 7 --merge"
RULE_TAIL = "Qoida: loyiha CLAUDE.md dagi merge qoidasi.\n"
SUB_REL = "docs/handbook"


TEMPLATE_DIR = tempfile.mkdtemp(prefix="merge-guard-tpl-")
TEMPLATES = {}


def tearDownModule():
    shutil.rmtree(HOME, ignore_errors=True)
    shutil.rmtree(TEMPLATE_DIR, ignore_errors=True)


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


def stamp_path(repo, name):
    return Path(repo) / git(repo, "rev-parse", "--git-path", name)


def template(kind):
    """(papka, head, submodule head): namunaviy repo bir marta quriladi, har test uni nusxalaydi (har testda 10 ta git chaqiruvi o'rniga).
    main ← feature (head = change.txt commiti); kind "sub": ichida docs/handbook submodule i, uning docs-fix branchi (sub head = 01.md commiti)."""
    if kind not in TEMPLATES:
        root = Path(TEMPLATE_DIR) / kind
        root.mkdir()
        repo = root / "repo"
        git(root, "init", "-q", "-b", "main", str(repo))
        commit(repo, "base.txt")
        git(repo, "switch", "-q", "-c", "feature")
        head, sub_head = commit(repo, "change.txt"), None
        if kind == "sub":
            sub_src = root / "sub-src"
            git(root, "init", "-q", "-b", "main", str(sub_src))
            commit(sub_src, "tools/check.py", "print('ok')\n")
            git(repo, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(sub_src), SUB_REL)
            git(repo / SUB_REL, "switch", "-q", "-c", "docs-fix")
            sub_head = commit(repo / SUB_REL, "01.md")
        TEMPLATES[kind] = (repo, head, sub_head)
    return TEMPLATES[kind]


class HookTestCase(unittest.TestCase):
    CONFIG = SOS
    TEMPLATE = "plain"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="merge-guard-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.gh_dir = self.tmp / "gh"
        (self.gh_dir / "bin").mkdir(parents=True)
        fake = self.gh_dir / "bin" / "gh"
        fake.write_text(FAKE_GH)
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        self.cfg_path = self.tmp / "quality-config.json"
        self.cfg_path.write_text(json.dumps(self.CONFIG))
        self.env = dict(BASE_ENV, FAKE_GH_DIR=str(self.gh_dir), PATH=f"{self.gh_dir / 'bin'}{os.pathsep}{os.environ['PATH']}",
                        QUALITY_CONFIG=str(self.cfg_path))
        self.repo = self.tmp / "repo"
        src, self.head, self.sub_head = template(self.TEMPLATE)
        shutil.copytree(src, self.repo, symlinks=True)
        self.tree = git(self.repo, "rev-parse", f"{self.head}^{{tree}}")
        self.set_pr(self.repo, 7, self.head)

    # --- iz nomlari sozlamadan ---------------------------------------------------------------------------------------------------------
    @property
    def ci_name(self):
        return f"{self.CONFIG['prefix']}-ci-ok"

    def review_name(self, pr=7):
        return f"{self.CONFIG['prefix']}-review-ok-{pr}"

    def set_pr(self, repo, number, head):
        (self.gh_dir / f"{Path(repo).name}.json").write_text(json.dumps({"number": number, "headRefOid": head}))

    def add_pr(self, number, head, repo=None):
        (self.gh_dir / f"{Path(repo or self.repo).name}.{number}.json").write_text(json.dumps({"number": number, "headRefOid": head}))

    def ci_stamp(self, repo=None, tree=None):
        stamp_path(repo or self.repo, self.ci_name).write_text(f"{tree or self.tree} 2026-10-24T10:00:00Z\n")

    def review_path(self, pr=7, repo=None):
        """Ko'rik izi PR bo'yicha: `<prefix>-review-ok-<PR>` (hook merge qilinayotgan PR ning faylini o'qiydi)."""
        return stamp_path(repo or self.repo, self.review_name(pr))

    def review_stamp(self, repo=None, tree=None, pr=7, head=None, round=1, blockers=0, major=0, minor=0, file_name=None):
        """file_name: faqat mos kelmaslik holatlari uchun (PR maydoni boshqa PR niki bo'lgan fayl, eski umumiy fayl); odatda fayl `-<pr>`."""
        repo = repo or self.repo
        path = stamp_path(repo, file_name) if file_name else self.review_path(pr, repo)
        path.write_text(
            f"{tree or self.tree} {pr} {head or self.head} 2026-10-24T10:05:00Z round={round} blockers={blockers} major={major} minor={minor}\n")

    def hook(self, command, cwd=None, agent=None, env=None, event=None):
        payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd or self.repo)}
        if agent is not None:
            payload["agent_type"] = agent
        payload.update(event or {})
        return subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload), capture_output=True, text=True, env=env or self.env, cwd=str(self.tmp))

    def run_hook(self, cmd, **kw):
        """(natija, soxta gh chaqiruvlari ro'yxati): har yurish o'z jurnal faylida, shuning uchun parallel yuritish xavfsiz."""
        log = self.gh_dir / f"calls-{next(SEQ)}.log"
        r = self.hook(cmd, env=dict(kw.pop("env", None) or self.env, FAKE_GH_LOG=str(log)), **kw)
        return r, (log.read_text().splitlines() if log.exists() else [])

    def run_many(self, cmds, **kw):
        """cmds ni parallel yuritadi; [(natija, chaqiruvlar), ...] buyruqlar tartibida."""
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            return list(pool.map(lambda c: self.run_hook(c, **kw), cmds))

    def assertBlocked(self, r, text=None):
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertTrue(r.stderr.startswith("Merge bloklandi: "), r.stderr)
        self.assertTrue(r.stderr.endswith(RULE_TAIL), r.stderr)
        self.assertEqual(r.stderr.count("\n"), 1, r.stderr)
        if text:
            self.assertIn(text, r.stderr)

    def assertAllowed(self, r):
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr, "")


class MergeGateTests(HookTestCase):
    def test_hook_is_executable_python_with_shebang(self):
        self.assertTrue(os.access(HOOK, os.X_OK))
        self.assertEqual(HOOK.read_text().splitlines()[0], "#!/usr/bin/env python3")
        self.assertEqual(subprocess.run([sys.executable, "-m", "py_compile", str(HOOK)], capture_output=True, cwd=str(self.tmp)).returncode, 0)
        r = subprocess.run([str(HOOK)], input=json.dumps({"tool_input": {"command": MERGE}, "cwd": str(self.repo)}), capture_output=True, text=True,
                           env=self.env, cwd=str(self.tmp))  # shebang orqali to'g'ridan-to'g'ri
        self.assertBlocked(r, "make ci yashil natijasi yo'q")

    def test_no_ci_stamp_blocks(self):
        r = self.hook(MERGE)
        self.assertBlocked(r, "make ci yashil natijasi yo'q")
        self.assertIn("sos-ci-ok", r.stderr)

    def test_no_ci_stamp_blocks_even_with_review_stamp(self):
        self.review_stamp()
        self.assertBlocked(self.hook(MERGE), "make ci")

    def test_stale_ci_stamp_blocks(self):
        self.ci_stamp(tree="0" * 40)
        self.review_stamp()
        self.assertBlocked(self.hook(MERGE), "make ci boshqa kod uchun")

    def test_ci_ok_without_review_blocks(self):
        self.ci_stamp()
        r = self.hook(MERGE)
        self.assertBlocked(r, "mustaqil ko'rik izi yo'q")
        self.assertIn("opus-reviewer", r.stderr)

    def test_review_for_unknown_head_and_other_tree_blocks(self):
        """Daraxt boshqa va iz head i lokal repoda yo'q (ajdod ekanini aniqlab bo'lmaydi): yopiq."""
        self.ci_stamp()
        self.review_stamp(tree="1" * 40, head="1" * 40)
        r = self.hook(MERGE)
        self.assertBlocked(r, "mustaqil ko'rik eskirgan")
        self.assertIn("opus-reviewer", r.stderr)

    def test_review_pr_mismatch_blocks(self):
        """Fayl 7-PR niki (`-7`), ichidagi PR maydoni 8: iz boshqa PR uchun yozilgan."""
        self.ci_stamp()
        self.review_stamp(pr=8, file_name=self.review_name(7))
        r = self.hook(MERGE)
        self.assertBlocked(r, "boshqa PR uchun")
        self.assertIn("opus-reviewer", r.stderr)

    def test_review_stamp_file_of_other_pr_is_not_used(self):
        """Iz PR bo'yicha: 8-PR ning izi (`-8`) 7-PR ni merge qilishga yaramaydi, hook `-7` ni qidiradi."""
        self.ci_stamp()
        self.review_stamp(pr=8)
        r = self.hook(MERGE)
        self.assertBlocked(r, "mustaqil ko'rik izi yo'q yoki buzuq")
        self.assertIn("sos-review-ok-7", r.stderr)
        self.assertIn("opus-reviewer", r.stderr)

    def test_legacy_shared_review_stamp_is_ignored(self):
        """PR bo'yicha izlardan oldingi umumiy `<prefix>-review-ok` to'liq yaroqli bo'lsa ham merge ga yaramaydi va PR izi bilan to'qnashmaydi."""
        self.ci_stamp()
        self.review_stamp(file_name="sos-review-ok")
        r = self.hook(MERGE)
        self.assertBlocked(r, "mustaqil ko'rik izi yo'q yoki buzuq")
        self.assertIn("sos-review-ok-7", r.stderr)
        self.review_stamp()
        self.assertAllowed(self.hook(MERGE))

    def test_stamp_file_follows_the_pr_number_gh_reports(self):
        """Nishonsiz merge: PR raqamini faqat gh aytadi, iz fayli shu raqam bo'yicha."""
        self.set_pr(self.repo, 9, self.head)
        self.ci_stamp()
        self.review_stamp()  # 7-PR izi, joriy branch PR i endi 9
        r = self.hook("gh pr merge --merge")
        self.assertBlocked(r, "mustaqil ko'rik izi yo'q yoki buzuq")
        self.assertIn("sos-review-ok-9", r.stderr)
        self.review_stamp(pr=9)
        self.assertAllowed(self.hook("gh pr merge --merge"))

    def test_incomplete_review_stamp_blocks(self):
        self.ci_stamp()
        for content in ("", f"{self.tree}\n", f"{self.tree} 7\n", f"{self.tree} 7 {self.head}\n"):
            with self.subTest(content=content):
                self.review_path().write_text(content)
                self.assertBlocked(self.hook(MERGE), "mustaqil ko'rik izi yo'q yoki buzuq")

    def test_both_stamps_ok_allows(self):
        self.ci_stamp()
        self.review_stamp()
        self.assertAllowed(self.hook(MERGE))

    def test_works_without_explicit_pr_number(self):
        self.ci_stamp()
        self.assertBlocked(self.hook("gh pr merge --merge"))
        self.review_stamp()
        r, calls = self.run_hook("gh pr merge --merge")
        self.assertAllowed(r)
        self.assertEqual(calls, ["repo: pr view --json number,headRefOid"])

    def test_review_for_same_tree_but_other_head_sha_still_ok(self):
        """Daraxt bir xil (masalan commit xabari o'zgargan): ko'rik hali kuchda."""
        self.ci_stamp()
        self.review_stamp(head="2" * 40)
        self.assertAllowed(self.hook(MERGE))

    def test_new_commit_after_review_blocks(self):
        self.ci_stamp()
        self.review_stamp()
        head2 = commit(self.repo, "more.txt")
        self.set_pr(self.repo, 7, head2)
        self.assertBlocked(self.hook(MERGE), "make ci boshqa kod uchun")

    def test_unknown_pr_blocks(self):
        (self.gh_dir / "repo.json").unlink()
        self.assertBlocked(self.hook(MERGE), "PR head commit i aniqlanmadi")

    def test_head_not_local_blocks(self):
        self.set_pr(self.repo, 7, "f" * 40)
        self.assertBlocked(self.hook(MERGE), "git fetch")

    def test_garbage_gh_output_blocks(self):
        for body in ("not json", "[]", "null", json.dumps({"number": 7}), json.dumps({"number": 7, "headRefOid": "--help"}),
                     json.dumps({"number": "7", "headRefOid": self.head}), json.dumps({"number": 0, "headRefOid": self.head})):
            with self.subTest(body=body):
                (self.gh_dir / "repo.json").write_text(body)
                self.assertBlocked(self.hook(MERGE), "PR head commit i aniqlanmadi")

    def test_other_command_forms_are_gated(self):
        cmds = [cmd.replace("/x/y", str(self.repo)) for cmd in (
            "cd /x/y && gh pr merge 7", "git status; gh pr merge 7 --squash", "command gh pr merge 7", "true | gh pr merge 7",
            "echo a\ngh pr merge 7", "/opt/homebrew/bin/gh pr merge 7", 'gh "pr" merge 7', "gh pr 'merge' 7",
            "if true; then gh pr merge 7; fi", "{ gh pr merge 7; }", "for i in 1; do gh pr merge 7; done", "! gh pr merge 7")]
        for cmd, (r, _) in zip(cmds, self.run_many(cmds)):
            with self.subTest(cmd=cmd):
                self.assertBlocked(r, "make ci yashil natijasi yo'q")

    def test_every_merge_of_a_command_is_checked(self):
        """Eski hook faqat birinchi merge ni ko'rardi; bu yerda ikkinchisi ham tekshiriladi."""
        self.add_pr(8, self.head)
        self.ci_stamp()
        self.review_stamp()
        self.assertAllowed(self.hook("gh pr merge 7 --merge"))
        r, calls = self.run_hook("gh pr merge 7 --merge && gh pr merge 8 --merge")
        self.assertBlocked(r, "mustaqil ko'rik izi yo'q")
        self.assertIn("sos-review-ok-8", r.stderr)
        self.assertEqual(calls, ["repo: pr view 7 --json number,headRefOid", "repo: pr view 8 --json number,headRefOid"])

    def test_worktree_uses_its_own_stamps(self):
        """Worktree da iz o'z git papkasida: asosiy checkout ning yaroqli izlari unga yaramaydi."""
        wt = self.tmp / "wt"
        git(self.repo, "worktree", "add", "-q", "-b", "wt-branch", str(wt))
        head = commit(wt, "wt.txt")
        self.set_pr(wt, 7, head)
        self.ci_stamp()
        self.review_stamp()
        self.assertBlocked(self.hook(MERGE, cwd=wt), "make ci yashil natijasi yo'q")
        tree = git(wt, "rev-parse", f"{head}^{{tree}}")
        self.ci_stamp(repo=wt, tree=tree)
        self.review_stamp(repo=wt, tree=tree, head=head)
        self.assertIn("worktrees", str(stamp_path(wt, self.ci_name)))
        self.assertAllowed(self.hook(MERGE, cwd=wt))

    def test_event_cwd_outside_repo_falls_back_to_project_dir(self):
        self.ci_stamp()
        self.review_stamp()
        outside = self.tmp / "outside"
        outside.mkdir()
        self.assertBlocked(self.hook(MERGE, cwd=outside), "PR head commit i aniqlanmadi")
        self.assertAllowed(self.hook(MERGE, cwd=outside, env=dict(self.env, CLAUDE_PROJECT_DIR=str(self.repo))))

    def test_gh_missing_or_failing_blocks(self):
        (self.gh_dir / "bin" / "gh").unlink()
        env = dict(self.env, PATH="/usr/bin:/bin")
        if shutil.which("gh", path="/usr/bin:/bin"):
            self.skipTest("tizimda haqiqiy gh bor")
        self.assertBlocked(self.hook(MERGE, env=env), "PR head commit i aniqlanmadi")


class ReviewVerdictTests(HookTestCase):
    """Izdagi hukm: `round=<k> blockers=<b> major=<m> minor=<n>`; bu yerda iz daraxti PR head daraxti bilan bir xil."""

    def setUp(self):
        super().setUp()
        self.ci_stamp()

    def write_stamp(self, tail):
        self.review_path().write_text(f"{self.tree} 7 {self.head} 2026-10-24T10:05:00Z{tail}\n")

    def test_old_four_field_stamp_blocks(self):
        self.write_stamp("")
        r = self.hook(MERGE)
        self.assertBlocked(r, "mustaqil ko'rik izi yo'q yoki buzuq")
        self.assertIn("eski shakl", r.stderr)
        self.assertIn("opus-reviewer", r.stderr)

    def test_stamp_with_missing_or_non_numeric_policy_fields_blocks(self):
        for tail in (" round=1 blockers=0", " round=1 major=0", " blockers=0 major=0", " round=x blockers=0 major=0",
                     " round=1 blockers=-1 major=0", " round=1 blockers=0 major=", " round=1.5 blockers=0 major=0", " minor=3", " note=1 blockers=0"):
            with self.subTest(tail=tail):
                self.write_stamp(tail)
                self.assertBlocked(self.hook(MERGE), "eski shakl")

    def test_blockers_in_stamp_block(self):
        for k in (1, 2, 3):
            with self.subTest(round=k):
                self.review_stamp(round=k, blockers=2)
                r = self.hook(MERGE)
                self.assertBlocked(r, "bloker bor")
                self.assertIn(f"blockers=2, {k}-tur", r.stderr)
                self.assertIn("opus-reviewer", r.stderr)

    def test_first_round_with_major_blocks(self):
        self.review_stamp(round=1, major=2, minor=1)
        r = self.hook(MERGE)
        self.assertBlocked(r, "1-ko'rikda muhim")
        self.assertIn("major=2", r.stderr)
        self.assertIn("2-ko'rik shart", r.stderr)
        self.assertIn("opus-reviewer", r.stderr)

    def test_second_round_with_major_allows(self):
        self.review_stamp(round=2, major=2)
        self.assertAllowed(self.hook(MERGE))

    def test_later_rounds_with_major_allow(self):
        for k in (3, 4):
            with self.subTest(round=k):
                self.review_stamp(round=k, major=1)
                self.assertAllowed(self.hook(MERGE))

    def test_first_round_with_only_minor_allows(self):
        self.review_stamp(round=1, major=0, minor=5)
        self.assertAllowed(self.hook(MERGE))

    def test_last_policy_token_wins_and_extra_tokens_are_ignored(self):
        self.write_stamp(" round=1 blockers=3 major=0 blockers=0 minor=1 extra=x")
        self.assertAllowed(self.hook(MERGE))


class ReviewAncestryTests(HookTestCase):
    """Ko'rikdan keyin daraxt o'zgargan: iz head i joriy head ning ajdodi va darvoza joriy daraxt uchun yashil bo'lsa merge mumkin."""

    def setUp(self):
        super().setUp()
        self.ci_stamp()
        self.review_stamp()

    def advance(self, name="more.txt"):
        """Ko'rikdan keyingi commit: PR head yangilanadi (self.head ko'rilgan head qoladi); qaytaradi (head, daraxt)."""
        head = commit(self.repo, name)
        self.set_pr(self.repo, 7, head)
        return head, git(self.repo, "rev-parse", f"{head}^{{tree}}")

    def test_commit_after_review_with_fresh_ci_allows(self):
        _, tree = self.advance()
        self.ci_stamp(tree=tree)
        self.assertAllowed(self.hook(MERGE))

    def test_several_commits_after_review_allow(self):
        self.advance("a.txt")
        self.advance("b.txt")
        _, tree = self.advance("c.txt")
        self.ci_stamp(tree=tree)
        self.assertAllowed(self.hook(MERGE))

    def test_main_merge_after_review_allows(self):
        git(self.repo, "switch", "-q", "main")
        commit(self.repo, "news.txt")
        git(self.repo, "switch", "-q", "feature")
        git(self.repo, "merge", "-q", "--no-edit", "main")
        self.assertEqual(len(git(self.repo, "rev-list", "--parents", "-n1", "HEAD").split()), 3)
        head = git(self.repo, "rev-parse", "HEAD")
        self.set_pr(self.repo, 7, head)
        self.ci_stamp(tree=git(self.repo, "rev-parse", f"{head}^{{tree}}"))
        self.assertAllowed(self.hook(MERGE))

    def test_verdict_rules_still_apply_after_new_commit(self):
        _, tree = self.advance()
        self.ci_stamp(tree=tree)
        for kwargs, text in (({"blockers": 1}, "bloker bor"), ({"major": 1}, "1-ko'rikda muhim"),
                             ({"pr": 8, "file_name": self.review_name(7)}, "boshqa PR uchun")):
            with self.subTest(**kwargs):
                self.review_stamp(**kwargs)
                self.assertBlocked(self.hook(MERGE), text)
        self.review_stamp(round=2, major=1)
        self.assertAllowed(self.hook(MERGE))

    def test_review_head_on_diverged_branch_blocks(self):
        git(self.repo, "switch", "-q", "-c", "rival", "main")
        rival = commit(self.repo, "rival.txt")
        git(self.repo, "switch", "-q", "feature")
        self.review_stamp(tree=git(self.repo, "rev-parse", f"{rival}^{{tree}}"), head=rival)
        r = self.hook(MERGE)
        self.assertBlocked(r, "ajdodi emas")
        self.assertIn("opus-reviewer", r.stderr)

    def test_rewritten_history_after_review_blocks(self):
        (self.repo / "change.txt").write_text("qayta yozilgan")
        git(self.repo, "commit", "-q", "--amend", "--no-edit", "-a")
        head = git(self.repo, "rev-parse", "HEAD")
        self.assertNotEqual(head, self.head)
        self.set_pr(self.repo, 7, head)
        self.ci_stamp(tree=git(self.repo, "rev-parse", f"{head}^{{tree}}"))
        self.assertBlocked(self.hook(MERGE), "ajdodi emas")

    def test_review_of_newer_commit_than_pr_head_blocks(self):
        """`--is-ancestor` argumentlari tartibi: ko'rilgan commit PR head idan keyingi bo'lsa ajdod emas."""
        newer = commit(self.repo, "newer.txt")
        self.review_stamp(tree=git(self.repo, "rev-parse", f"{newer}^{{tree}}"), head=newer)
        self.assertBlocked(self.hook(MERGE), "ajdodi emas")

    def test_review_head_that_is_not_a_hash_blocks(self):
        """Iz head i git ga optsiya bo'lib tushmasligi kerak."""
        self.review_stamp(tree="3" * 40, head="--help")
        self.assertBlocked(self.hook(MERGE), "ajdodi emas")


class TargetParsingTests(HookTestCase):
    """Nishon PR: bayroqlar, env prefikslari va nishon turlari. Joriy branch PR 7 ning izlari yaroqli, PR 8 boshqa daraxtda:
    hook PR 8 o'rniga joriy branch PR ini tekshirib qo'ysa buyruq o'tib ketadi (yoki PR 8 izlari bilan bloklanadi)."""

    TARGET8 = [
        "gh pr merge --merge 8", "gh pr merge --squash 8", "gh pr merge --rebase 8", "gh pr merge --merge --delete-branch 8",
        "gh pr merge --admin --auto 8", "gh pr merge -d -m 8", "gh pr merge -dm 8", "gh pr merge 8 --merge", "gh pr merge 8",
        'gh pr merge -b "x" 8', "gh pr merge -b 'x y' 8", 'gh pr merge --body "x" 8', "gh pr merge --body=x 8", 'gh pr merge --body="x y" 8',
        "gh pr merge -bx 8", "gh pr merge -db x 8", 'gh pr merge -b "a; b | c & d" 8', 'gh pr merge --body "a\nb" 8',
        "gh pr merge -F body.md 8", "gh pr merge --body-file=body.md 8", "gh pr merge -t subj 8", "gh pr merge --subject subj 8",
        "gh pr merge -A a@b.c 8", "gh pr merge --author-email a@b.c --merge 8", "gh pr merge --match-head-commit abc123 8",
        "gh pr merge --match-head-commit=abc123 8", "gh pr merge -t 5 -b 6 8", "gh pr merge -- 8", "gh pr merge --merge -- 8",
        'gh pr merge "8"', "gh pr merge '8'", "gh pr merge 08", "gh pr merge   --merge   8  ", "gh pr merge --merge 8 --delete-branch",
        "gh pr merge 8 -b x", "gh pr merge 8; echo 9", "gh pr merge 8 && gh pr view 9", "gh pr merge 8 2>&1", "(gh pr merge --merge 8)",
        "gh pr merge 8 > out.log", "gh pr merge 8 >out.log 2>&1", "gh pr merge 8 | cat", "gh pr merge 8 # sharh 9", "gh pr merge --merge 8 <<< y",
        "gh pr merge 8 --body-file - <<'EOF'\nmatn: gh pr merge 7\nEOF", "gh pr merge 8 --body \"$(cat <<'EOF'\nxulosa\nEOF\n)\"",
    ]
    PREFIXED8 = [
        "FOO=1 gh pr merge 8", "A=b C=d gh pr merge 8", "env gh pr merge 8", "env A=b gh pr merge 8", "env A=b C=d gh pr merge --merge 8",
        'A="b c" gh pr merge 8', "A='b c' gh pr merge 8", "A=b env C=d gh pr merge 8", "FOO=1 command gh pr merge 8",
        "NOT_GH_REPO=x gh pr merge 8", "GH_REPO_X=1 gh pr merge 8", "echo a; FOO=1 gh pr merge --squash 8", "true && env FOO=1 gh pr merge 8",
        "(FOO=1 gh pr merge 8)", "echo a\nFOO=1 gh pr merge 8",
    ]
    WRAPPED8 = [
        "time gh pr merge --merge 8", "time -p gh pr merge 8", "nohup gh pr merge 8", "nohup gh pr merge 8 > out.log 2>&1 &", "sudo gh pr merge 8",
        "sudo -u x gh pr merge 8", "sudo -E -u deploy gh pr merge --squash 8", "sudo --user=x gh pr merge 8", "sudo --user x gh pr merge 8",
        "exec gh pr merge 8", "nice gh pr merge 8", "nice -n 10 gh pr merge 8", "nice -n -5 gh pr merge 8", "nice -10 gh pr merge 8",
        "nice --adjustment=5 gh pr merge 8", "timeout 30 gh pr merge 8", "timeout 30s gh pr merge 8", "timeout 1.5m gh pr merge --merge 8",
        "timeout -s KILL 30 gh pr merge 8", "timeout --signal=KILL -k 5 30 gh pr merge 8", "timeout --foreground 30 gh pr merge 8",
        "command -p gh pr merge 8", "env time gh pr merge 8", "env -u FOO gh pr merge 8", "FOO=1 nohup nice -n 5 timeout 30 gh pr merge --merge 8",
        "sudo env FOO=1 time -p gh pr merge 8", "echo a; time gh pr merge 8", "true && nohup gh pr merge 8", "(time gh pr merge 8)",
        "echo a\nsudo gh pr merge 8",
    ]
    FLAGGED8 = [  # `gh` bilan `pr` va `pr` bilan `merge` orasidagi bayroq (qiymatsiz) merge ni yashirmaydi
        "gh pr --help merge 8", "gh pr -h merge --merge 8", "gh --help pr merge 8", "gh pr --help merge 8 --delete-branch", "FOO=1 gh pr --help merge 8",
        "time gh pr -h merge 8", "(gh pr --help merge 8)", "gh pr --help --help merge 8", "gh pr merge --help 8",
    ]
    CALL8 = "repo: pr view 8 --json number,headRefOid"
    CALL_CURRENT = "repo: pr view --json number,headRefOid"

    def setUp(self):
        super().setUp()
        git(self.repo, "switch", "-q", "-c", "other", "main")
        self.head8 = commit(self.repo, "other.txt")
        self.tree8 = git(self.repo, "rev-parse", f"{self.head8}^{{tree}}")
        git(self.repo, "switch", "-q", "feature")
        self.add_pr(8, self.head8)
        self.ci_stamp()
        self.review_stamp()  # PR 7 (joriy branch)

    def test_fixture_current_branch_pr_passes(self):
        self.assertAllowed(self.hook("gh pr merge 7"))
        self.assertAllowed(self.hook("gh pr merge"))

    def test_target_pr_is_checked_not_current_branch_pr(self):
        cmds = self.TARGET8 + self.PREFIXED8 + self.WRAPPED8 + self.FLAGGED8
        for cmd, (r, calls) in zip(cmds, self.run_many(cmds)):
            with self.subTest(cmd=cmd):
                self.assertBlocked(r, "make ci boshqa kod uchun")
                self.assertIn(self.head8, r.stderr)
                self.assertEqual(calls, [self.CALL8])

    def test_target_pr_allows_with_its_own_stamps(self):
        self.ci_stamp(tree=self.tree8)
        self.review_stamp(tree=self.tree8, pr=8, head=self.head8)
        cmds = self.TARGET8 + self.PREFIXED8 + self.WRAPPED8 + self.FLAGGED8
        for cmd, (r, calls) in zip(cmds, self.run_many(cmds)):
            with self.subTest(cmd=cmd):
                self.assertAllowed(r)
                self.assertEqual(calls, [self.CALL8])

    def test_review_stamp_of_other_pr_still_blocks_target(self):
        """Daraxt, head va darvoza 8-PR niki, lekin ko'rik izi 7-PR niki: fayl nomi bo'yicha 8 ning o'z izi yo'q; 8 ning faylida PR maydoni 7 bo'lsa ham blok."""
        self.ci_stamp(tree=self.tree8)
        self.review_stamp(tree=self.tree8, pr=7, head=self.head8)
        for cmd in ("gh pr merge --merge 8", "FOO=1 gh pr merge 8"):
            with self.subTest(cmd=cmd, file=self.review_name(7)):
                r = self.hook(cmd)
                self.assertBlocked(r, "mustaqil ko'rik izi yo'q yoki buzuq")
                self.assertIn("sos-review-ok-8", r.stderr)
        self.review_stamp(tree=self.tree8, pr=7, head=self.head8, file_name=self.review_name(8))
        for cmd in ("gh pr merge --merge 8", "FOO=1 gh pr merge 8"):
            with self.subTest(cmd=cmd, file=self.review_name(8)):
                self.assertBlocked(self.hook(cmd), "boshqa PR uchun")

    def test_two_prs_review_stamps_coexist_and_each_merge_uses_its_own(self):
        """Iz PR bo'yicha: ikki PR ning izlari yonma-yon turadi, har merge o'zinikiga qaraydi; biri yo'qolsa faqat shu PR bloklanadi."""
        self.review_stamp(tree=self.tree8, pr=8, head=self.head8)
        self.ci_stamp(tree=self.tree8)
        self.assertAllowed(self.hook("gh pr merge 8 --merge"))
        self.ci_stamp()
        self.assertAllowed(self.hook("gh pr merge 7 --merge"))
        self.assertAllowed(self.hook("gh pr merge --merge"))
        self.review_path(7).unlink()
        self.assertBlocked(self.hook("gh pr merge 7 --merge"), "mustaqil ko'rik izi yo'q")
        self.ci_stamp(tree=self.tree8)
        self.assertAllowed(self.hook("gh pr merge 8 --merge"))

    def test_no_target_uses_current_branch_pr(self):
        cmds = ["gh pr merge", "gh pr merge --merge", "gh pr merge --squash --delete-branch", 'gh pr merge -b "x"', "gh pr merge --body=x",
                "gh pr merge --body 8", "gh pr merge -b 8 --merge", "gh pr merge -F 8", "gh pr merge --match-head-commit 8", "gh pr merge -bx",
                "gh pr merge --merge; echo 8", "gh pr merge --merge && echo 8", "gh pr merge --merge | cat 8", "gh pr merge --merge\necho 8",
                "(gh pr merge --merge) && echo 8", "FOO=1 gh pr merge --merge", "env FOO=1 gh pr merge", "time gh pr merge --merge",
                "nohup gh pr merge", "sudo -u x gh pr merge --squash", "nice -n 10 gh pr merge --merge", "timeout 30s gh pr merge",
                "exec gh pr merge --merge; echo 8", "gh pr merge --merge # 8", "2>/dev/null gh pr merge --merge"]
        for cmd, (r, calls) in zip(cmds, self.run_many(cmds)):
            with self.subTest(cmd=cmd):
                self.assertAllowed(r)
                self.assertEqual(calls, [self.CALL_CURRENT])

    def test_no_target_blocks_when_current_branch_pr_has_no_review(self):
        self.review_path().unlink()
        r, calls = self.run_hook("FOO=1 gh pr merge --merge")
        self.assertBlocked(r, "mustaqil ko'rik izi yo'q")
        self.assertEqual(calls, [self.CALL_CURRENT])

    def test_non_number_target_blocks(self):
        cmds = ["gh pr merge https://github.com/acme/app/pull/8", "gh pr merge --merge https://github.com/o/r/pull/8",
                    "gh pr merge https://github.com/o/r/pull/7 --merge", "gh pr merge other", "gh pr merge --merge other",
                    "gh pr merge feature", "gh pr merge o:feature", "gh pr merge '#8'", "gh pr merge \\#8", "gh pr merge 8x",
                    "gh pr merge 0", "gh pr merge 00", "gh pr merge -b x feature", "gh pr merge -- -8", "gh pr merge -- --merge 8",
                    "gh pr merge #8", "gh pr merge --merge #8", "gh pr merge --merge #now", "gh pr merge #8 --merge",
                    "FOO=1 gh pr merge feature", "env gh pr merge https://github.com/o/r/pull/8", "gh pr merge $(gh pr list)",
                    "gh pr merge `echo 8`", "gh pr merge $PR", "gh pr merge >out 8", "gh pr merge 2>/dev/null 8", "(gh pr merge feature)",
                    "time gh pr merge feature", "sudo -u x gh pr merge https://github.com/o/r/pull/8", "timeout 30 gh pr merge other",
                    "nice -n 5 gh pr merge '#8'", "gh pr merge ٨"]
        for cmd, (r, calls) in zip(cmds, self.run_many(cmds)):
            with self.subTest(cmd=cmd):
                self.assertBlocked(r, "PR raqami")
                self.assertEqual(calls, [])

    def test_chdir_wrappers_block_because_the_repo_is_unknown(self):
        cmds = ["env -C /elsewhere gh pr merge 7", "env --chdir=/elsewhere gh pr merge 7", "env --chdir /elsewhere gh pr merge 7",
                "sudo -D /elsewhere gh pr merge 7", "sudo --chdir=/elsewhere gh pr merge 7", "FOO=1 env -C /elsewhere nice gh pr merge --merge"]
        for cmd, (r, calls) in zip(cmds, self.run_many(cmds)):
            with self.subTest(cmd=cmd):
                self.assertBlocked(r, "env -C / sudo -D bilan merge")
                self.assertEqual(calls, [])

    def test_comment_after_the_target_or_with_a_space_is_a_comment(self):
        self.ci_stamp(tree=self.tree8)
        self.review_stamp(tree=self.tree8, pr=8, head=self.head8)
        cmds = ["gh pr merge 8 #9", "gh pr merge 8 #sharh 9", "gh pr merge 8 # 9", "gh pr merge --merge 8 #!", "# gh pr merge 7\ngh pr merge 8"]
        for cmd, (r, calls) in zip(cmds, self.run_many(cmds)):
            with self.subTest(cmd=cmd):
                self.assertAllowed(r)
                self.assertEqual(calls, [self.CALL8])

    def test_unbalanced_quote_before_target_blocks(self):
        for cmd in ('gh pr merge -b "x 8', "gh pr merge -b 'x 8"):
            with self.subTest(cmd=cmd):
                r, calls = self.run_hook(cmd)
                self.assertBlocked(r, "tahlil qilinmadi")
                self.assertIn("tirnoqlar juftlanmagan", r.stderr)
                self.assertEqual(calls, [])

    def test_gh_repo_and_gh_host_env_block(self):
        cmds = ["GH_REPO=x gh pr merge 8", "GH_REPO=o/r gh pr merge --merge 8", "GH_HOST=x gh pr merge 8", "GH_HOST=github.example.com gh pr merge",
                    "FOO=1 GH_REPO=o/r gh pr merge 8", "GH_REPO=o/r FOO=1 gh pr merge 8", "env GH_REPO=o/r gh pr merge 8", "env FOO=1 GH_HOST=x gh pr merge 8",
                    'GH_REPO="o/r" gh pr merge 8', "GH_REPO= gh pr merge 8", "GH_HOST=x command gh pr merge 8", "cd /x && GH_REPO=o/r gh pr merge 8",
                    "time GH_REPO=o/r gh pr merge 8", "sudo env GH_HOST=x gh pr merge 8", "export GH_REPO=o/r; nohup gh pr merge 8"]
        cmds = [cmd.replace("/x", str(self.repo)) for cmd in cmds]
        for cmd, (r, calls) in zip(cmds, self.run_many(cmds)):
            with self.subTest(cmd=cmd):
                self.assertBlocked(r, "GH_REPO/GH_HOST")
                self.assertEqual(calls, [])

    def test_repo_flag_blocks_anywhere_in_the_merge_command(self):
        cmds = ["gh pr merge 7 -R acme/app", "gh pr merge -R acme/app 7", "gh pr merge 7 --repo acme/app", "gh pr merge --repo=acme/app 7",
                "gh pr merge -Racme/app 7", "gh pr merge -dR acme/app 7", "gh pr merge --merge 7 -d -R o/r", "FOO=1 gh pr merge -R o/r 7"]
        for cmd, (r, calls) in zip(cmds, self.run_many(cmds)):
            with self.subTest(cmd=cmd):
                self.assertBlocked(r, "-R/--repo")
                self.assertEqual(calls, [])

    def test_repo_flag_lookalikes_are_not_repo_flags(self):
        self.ci_stamp(tree=self.tree8)
        self.review_stamp(tree=self.tree8, pr=8, head=self.head8)
        cmds = ["gh pr merge -bRelease 8", 'gh pr merge -b "Release R" 8', "gh pr merge -tRelease 8", "gh pr merge -A Rex@x.y 8"]
        for cmd, (r, _) in zip(cmds, self.run_many(cmds)):
            with self.subTest(cmd=cmd):
                self.assertAllowed(r)

    def test_repo_flag_between_pr_and_merge_blocks(self):
        """`gh pr --repo o/r merge 5` da -R/--repo merge dan oldin: avval bu shakl merge deb tanilmay, izlarsiz o'tib ketardi."""
        cmds = ["gh pr --repo o/r merge 5 --merge", "gh pr -R o/r merge 5", "gh pr --repo=o/r merge 5", "gh pr -Ro/r merge 5", "gh pr -hR o/r merge 5",
                "gh pr --help --repo o/r merge 5", "gh pr -R merge merge 5", "gh --repo o/r pr merge 5", "gh -R o/r pr merge 5", "gh -R o/r pr -R o/r merge 5",
                "FOO=1 gh pr -R o/r merge 5", "env gh pr -R o/r merge", "time gh pr --repo o/r merge --squash 5", "(gh pr -R o/r merge 5)",
                "echo a; sudo -u x gh pr --repo o/r merge 5", "gh pr --repo o/r merge", "/opt/homebrew/bin/gh pr -R o/r merge 7"]
        for cmd, (r, calls) in zip(cmds, self.run_many(cmds)):
            with self.subTest(cmd=cmd):
                self.assertBlocked(r, "-R/--repo")
                self.assertEqual(calls, [])

    def test_gh_flags_before_other_subcommands_do_not_make_a_merge(self):
        cmds = ["gh pr --repo o/r view 8", "gh pr -R o/r checks 7", "gh pr --repo o/r comment 7 --body 'merge when ready'", "gh pr -R o/r list", "gh -R o/r pr view 8",
                "gh pr -R merge view 8", "gh pr --repo merge view", "gh pr -Ro/merge view 8", "gh pr --help", "gh pr -- merge 8", "gh --help", "gh pr -R o/r",
                "gh issue --repo o/r merge 8", "gh -R o/r repo merge 8"]
        for cmd, (r, calls) in zip(cmds, self.run_many(cmds)):
            with self.subTest(cmd=cmd):
                self.assertAllowed(r)
                self.assertEqual(calls, [])

    def test_flagged_merge_inside_shell_string_blocks(self):
        cmds = ["bash -c 'gh pr --repo o/r merge 8'", 'sh -c "gh pr -R o/r merge 8"', "eval 'gh pr --help merge 8'", "echo 'gh pr -R o/r merge 8' | sh",
                "bash <<< 'gh pr --repo o/r merge 8'", "echo $(gh pr -R o/r merge 8)", "bash -c 'gh --repo o/r pr merge 8'", "echo gh pr -Ro/r merge 8 | bash"]
        for cmd, (r, calls) in zip(cmds, self.run_many(cmds)):
            with self.subTest(cmd=cmd):
                self.assertBlocked(r, "qobiq qatori ichida")
                self.assertEqual(calls, [])

    def test_unbalanced_quote_with_flagged_merge_blocks(self):
        for cmd in ('gh pr --repo o/r merge -b "x 8', "gh pr -R o/r merge -b 'x 8", "gh --help pr --help merge -b 'x 8"):
            with self.subTest(cmd=cmd):
                r, calls = self.run_hook(cmd)
                self.assertBlocked(r, "tirnoqlar juftlanmagan")
                self.assertEqual(calls, [])

    def test_prefix_forms_are_not_mistaken_for_merge(self):
        for cmd in ("FOO=1 gh pr view 8", "env FOO=1 ls", "FOO=1 echo gh pr merge 8", "env echo gh pr merge 8", "GH_REPO=x gh pr view 8",
                    "echo FOO=1 gh pr merge 8", "FOO=1 gh pr create --title t", "export GH_REPO=x", "gh pr merged 8", "gh repo merge 8"):
            with self.subTest(cmd=cmd):
                self.assertAllowed(self.hook(cmd))

    def test_merge_inside_shell_string_blocks(self):
        cmds = ["bash -c 'gh pr merge 8'", 'sh -c "gh pr merge 8"', "zsh -c 'gh   pr   merge   8'", "bash -c 'gh\tpr\tmerge 8'",
                    'eval "gh pr merge 8"', "eval 'gh pr merge 8'", "eval gh pr merge 8", "/bin/bash -lc 'gh pr merge 8'", "dash -c 'gh pr merge 8'",
                    "sudo bash -c 'gh pr merge 8'", "env FOO=1 sh -c \"gh pr merge 8\"", "FOO=1 bash -c 'gh pr merge 8'",
                    "time bash -c 'gh pr merge 8'", "true && eval \"gh pr merge 8\"", "echo a; bash -c 'cd x; gh pr merge 8'",
                    "echo 'gh pr merge 8' | sh", "echo gh pr merge 8 | bash", "echo 'gh pr merge 8' | /bin/zsh -s", "bash <<< 'gh pr merge 8'",
                    "(sh -c 'gh pr merge 8')", "bash -c 'gh pr merge 8'\n", "bash -c \"gh 'pr' merge 8\"", "bash <<'EOF'\ngh pr merge 8\nEOF",
                    "cat <<EOF | sh\ngh pr merge 8\nEOF"]
        for cmd, (r, calls) in zip(cmds, self.run_many(cmds)):
            with self.subTest(cmd=cmd):
                self.assertBlocked(r, "qobiq qatori ichida")
                self.assertIn("gh pr merge <N>", r.stderr)
                self.assertEqual(calls, [])

    def test_merge_inside_substitution_blocks(self):
        cmds = ["echo $(gh pr merge 8)", "echo `gh pr merge 8`", 'echo "$(gh pr merge 8)"', "x=$(FOO=1 gh pr merge 8)", "echo $(echo $(gh pr merge 8))",
                'echo "`gh pr merge 8`"', "cat <<EOF\n$(gh pr merge 8)\nEOF"]
        for cmd, (r, calls) in zip(cmds, self.run_many(cmds)):
            with self.subTest(cmd=cmd):
                self.assertBlocked(r, "qobiq qatori ichida")
                self.assertEqual(calls, [])

    def test_merge_words_inside_quotes_or_other_shell_use_stay_allowed(self):
        cmds = ['echo "time gh pr merge 8"', "echo 'nohup gh pr merge 8'", 'echo "sudo gh pr merge 8" > note.txt', "grep 'nohup gh pr merge' README.md",
                    "echo \"run it in bash, then gh pr merge 8\"", "bash -c 'echo hi'", "bash infra/ci/local-ci.sh", "sh script.sh", "eval \"echo gh\"",
                    "git commit -m 'sh and bash: gh pr merge 8 later'", "bashful gh pr merge-notes", "ssh host 'gh pr merge 8'",
                    'echo $(echo "gh pr merge 8")', "echo '`gh pr merge 8`'", "bash -c 'echo \"gh pr merge 8\"'", "bash script.sh && echo 'gh pr merge 8'",
                    "gh pr comment 7 --body 'ready: gh pr merge 7 --merge'"]
        for cmd, (r, calls) in zip(cmds, self.run_many(cmds)):
            with self.subTest(cmd=cmd):
                self.assertAllowed(r)
                self.assertEqual(calls, [])

    def test_long_command_is_fast(self):
        r, calls = self.run_hook("FOO=1 " * 200 + "gh pr merge " + "--merge " * 500 + "8")
        self.assertBlocked(r, "make ci boshqa kod uchun")
        self.assertEqual(calls, [self.CALL8])


class NonMergeTests(HookTestCase):
    def test_non_merge_commands_pass(self):
        for cmd in ("echo hi", "gh pr view 7", "gh pr create --title t", "gh pr checks 7", "git push origin feature",
                    "echo gh pr merge 7", "grep 'gh pr merge' README.md", "ls"):
            with self.subTest(cmd=cmd):
                self.assertAllowed(self.hook(cmd))

    def test_merge_text_only_inside_heredoc_passes(self):
        cmd = "gh pr create --title t --body \"$(cat <<'EOF'\n## Summary\nThen review, then gh pr merge 7 --merge is used.\nEOF\n)\""
        self.assertAllowed(self.hook(cmd))
        cmd = "git commit -m \"$(cat <<'EOF'\nfix: reminder - gh pr merge 7 only after review\nEOF\n)\""
        self.assertAllowed(self.hook(cmd))
        cmd = "gh pr comment 7 --body-file - <<EOF\ngh pr merge 7\nEOF"
        self.assertAllowed(self.hook(cmd))

    def test_non_merge_commands_never_read_the_config(self):
        """Sozlama kerak bo'lganda yuklanadi: begona buyruq buzuq sozlamada ham jim o'tadi."""
        self.cfg_path.write_text("{ buzuq")
        for cmd in ("ls", "gh pr view 7", "echo gh pr merge 7"):
            with self.subTest(cmd=cmd):
                self.assertAllowed(self.hook(cmd))

    def test_broken_input_passes(self):
        for raw in ("", "not json", "{}", "[]", "null", json.dumps({"tool_input": {}}), json.dumps({"tool_input": {"command": 5}}),
                    json.dumps({"tool_input": {"command": ""}}), json.dumps({"tool_input": "gh pr merge 7"})):
            with self.subTest(raw=raw):
                r = subprocess.run([sys.executable, str(HOOK)], input=raw, capture_output=True, text=True, env=self.env, cwd=str(self.tmp))
                self.assertEqual(r.returncode, 0, r.stderr)

    def test_other_tool_names_pass(self):
        for tool in ("Read", "Agent", "Write"):
            with self.subTest(tool=tool):
                self.assertAllowed(self.hook(MERGE, event={"tool_name": tool}))

    def test_event_without_tool_name_is_still_checked(self):
        payload = json.dumps({"tool_input": {"command": MERGE}, "cwd": str(self.repo)})
        r = subprocess.run([sys.executable, str(HOOK)], input=payload, capture_output=True, text=True, env=self.env, cwd=str(self.tmp))
        self.assertBlocked(r, "make ci yashil natijasi yo'q")

    def test_non_ascii_command_in_ascii_locale_still_gated(self):
        """LANG=C da ham kirish UTF-8 sifatida o'qiladi: ruscha/o'zbekcha matn qo'shilgan merge o'tib ketmaydi."""
        env = dict(self.env, LANG="C", LC_ALL="C", PYTHONIOENCODING="ascii")
        r = self.hook("echo 'Тест oʻzbek'; gh pr merge 7 --merge", env=env)
        self.assertBlocked(r, "make ci yashil natijasi yo'q")

    def test_unexpected_exception_exits_1_without_blocking(self):
        """Hook xatosi ishni to'xtatmaydi: traceback va exit 1 (Claude Code uni bloklash deb hisoblamaydi). Xato sitecustomize orqali shlex ga solinadi."""
        boom = self.tmp / "boom"
        boom.mkdir()
        (boom / "sitecustomize.py").write_text("import shlex\n\ndef _boom(*a, **k):\n    raise RuntimeError('ichki xato')\n\nshlex.shlex = _boom\n")
        r = self.hook(MERGE, env=dict(self.env, PYTHONPATH=str(boom)))
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("RuntimeError: ichki xato", r.stderr)
        self.assertNotIn("Merge bloklandi", r.stderr)


class CdAndSubmoduleTests(HookTestCase):
    """Submodule o'z repo si va o'z PR lari bilan: `cd docs/handbook && gh pr merge 7` o'sha papkadagi repo ning izlarini tekshiradi."""

    TEMPLATE = "sub"

    def setUp(self):
        super().setUp()
        self.sub = self.repo / SUB_REL
        self.sub_tree = git(self.sub, "rev-parse", f"{self.sub_head}^{{tree}}")
        self.set_pr(self.sub, 7, self.sub_head)  # fayl nomi handbook.json
        # asosiy repo ning o'z izlari to'liq yaroqli: submodule PR i ularga tayana olmasligi kerak
        self.ci_stamp()
        self.review_stamp()

    def sub_stamps(self, tree=None, head=None):
        self.ci_stamp(repo=self.sub, tree=tree or self.sub_tree)
        self.review_stamp(repo=self.sub, tree=tree or self.sub_tree, head=head or self.sub_head)

    def test_submodule_stamps_live_in_the_submodule_git_dir(self):
        self.assertIn("modules", str(stamp_path(self.sub, self.ci_name)))
        self.assertNotEqual(stamp_path(self.sub, self.ci_name), stamp_path(self.repo, self.ci_name))

    def test_hook_from_submodule_cwd_uses_submodule_stamps(self):
        self.assertBlocked(self.hook(MERGE, cwd=self.sub), "make ci yashil natijasi yo'q")
        self.ci_stamp(repo=self.sub, tree=self.sub_tree)
        self.assertBlocked(self.hook(MERGE, cwd=self.sub), "mustaqil ko'rik izi yo'q")
        self.review_stamp(repo=self.sub, tree=self.sub_tree, head=self.sub_head)
        r, calls = self.run_hook(MERGE, cwd=self.sub)
        self.assertAllowed(r)
        self.assertEqual(calls, ["handbook: pr view 7 --json number,headRefOid"])

    def test_cd_into_submodule_uses_submodule_stamps_not_root_ones(self):
        cmd = f"cd {SUB_REL} && " + MERGE
        self.assertBlocked(self.hook(cmd, cwd=self.repo), "make ci yashil natijasi yo'q")  # asosiy repo izlari yaroqli, submodule da yo'q
        self.sub_stamps()
        self.assertAllowed(self.hook(cmd, cwd=self.repo))
        self.assertAllowed(self.hook(f"cd {self.sub} && {MERGE}", cwd=self.tmp))
        self.assertAllowed(self.hook(f"cd '{self.sub}' && {MERGE}", cwd=self.tmp))
        self.assertAllowed(self.hook(f'cd "{self.sub}" && {MERGE}', cwd=self.tmp))
        self.assertAllowed(self.hook(f"cd -P {SUB_REL}; {MERGE}", cwd=self.repo))
        self.assertAllowed(self.hook(f"cd -- {SUB_REL} && {MERGE}", cwd=self.repo))
        self.assertAllowed(self.hook("cd docs && cd handbook && " + MERGE, cwd=self.repo))
        self.assertAllowed(self.hook(f"pushd {SUB_REL} && {MERGE}", cwd=self.repo))
        # asosiy repo ning o'zida (cd siz) o'z izlari ishlaydi
        self.assertAllowed(self.hook(MERGE, cwd=self.repo))

    def test_tilde_in_cd_target_is_expanded(self):
        self.sub_stamps()
        link = Path(HOME) / "handbook-link"
        link.symlink_to(self.sub)
        self.addCleanup(link.unlink)
        self.assertAllowed(self.hook("cd ~/handbook-link && " + MERGE, cwd=self.repo))

    def test_commit_after_review_passes_via_ancestry_in_submodule_repo(self):
        self.sub_stamps()
        head2 = commit(self.sub, "02.md")
        self.set_pr(self.sub, 7, head2)
        cmd = f"cd {SUB_REL} && " + MERGE
        self.assertBlocked(self.hook(cmd, cwd=self.repo), "make ci boshqa kod uchun")
        self.ci_stamp(repo=self.sub, tree=git(self.sub, "rev-parse", f"{head2}^{{tree}}"))
        self.assertAllowed(self.hook(cmd, cwd=self.repo))

    def test_cd_out_of_submodule_back_to_root(self):
        self.assertAllowed(self.hook(f"cd {SUB_REL} && cd ../.. && " + MERGE, cwd=self.repo))
        self.assertAllowed(self.hook(f"cd {self.sub} && cd .. && cd .. && " + MERGE, cwd=self.tmp))

    def test_unresolvable_or_missing_cd_target_blocks(self):
        for cmd, text in (('cd "$DOCS" && ' + MERGE, "cd nishoni"), ("cd `pwd` && " + MERGE, "cd nishoni"), ("cd $HOME/x && " + MERGE, "cd nishoni"),
                          ('cd "$(git rev-parse --show-toplevel)" && ' + MERGE, "cd nishoni"), ("cd - && " + MERGE, "cd nishoni"),
                          ("cd yoq/papka && " + MERGE, "papka yo'q"), (f"cd {SUB_REL}/yoq && " + MERGE, "papka yo'q"),
                          ("cd $X; cd docs && " + MERGE, "cd nishoni"), ("popd && " + MERGE, "popd")):
            with self.subTest(cmd=cmd):
                self.assertBlocked(self.hook(cmd, cwd=self.repo), text)

    def test_absolute_cd_resets_unknown_target(self):
        self.assertAllowed(self.hook(f'cd "$X" ; cd {self.repo} && ' + MERGE, cwd=self.tmp))
        self.assertBlocked(self.hook(f'cd "$X" ; cd docs && ' + MERGE, cwd=self.repo), "cd nishoni")

    def test_cd_in_pipeline_background_and_subshell_does_not_leak(self):
        """cd quvur elementi, fon ishi yoki ( ) ichida bo'lsa keyingi merge o'sha papkada emas, joriy repo da bajariladi."""
        self.assertAllowed(self.hook(f"cd {SUB_REL} | {MERGE}", cwd=self.repo))
        self.assertAllowed(self.hook(f"cd {SUB_REL} & {MERGE}", cwd=self.repo))
        self.assertAllowed(self.hook(f"(cd {SUB_REL}; true); {MERGE}", cwd=self.repo))
        self.assertAllowed(self.hook(f"(cd {SUB_REL} && true) && {MERGE}", cwd=self.repo))
        self.assertBlocked(self.hook(f"(cd {SUB_REL} && {MERGE})", cwd=self.repo), "make ci yashil natijasi yo'q")
        self.assertBlocked(self.hook(f"cd {SUB_REL} && true && {MERGE}", cwd=self.repo), "make ci yashil natijasi yo'q")
        self.assertBlocked(self.hook(f"true | (cd {SUB_REL}; {MERGE})", cwd=self.repo), "make ci yashil natijasi yo'q")

    def test_merge_after_cd_then_merge_in_root(self):
        """Ikkita merge: birinchisi submodule da, ikkinchisi cd .. dan keyin asosiy repo da; ikkalasi o'z izlari bilan."""
        self.sub_stamps()
        self.assertAllowed(self.hook(f"cd {SUB_REL} && {MERGE} && cd ../.. && {MERGE}", cwd=self.repo))
        stamp_path(self.sub, self.ci_name).unlink()
        self.assertBlocked(self.hook(f"cd {SUB_REL} && {MERGE} && cd ../.. && {MERGE}", cwd=self.repo), "make ci yashil natijasi yo'q")

    def test_repo_flag_blocks_in_submodule_context(self):
        for cmd in (MERGE + " -R acme/handbook", MERGE + " --repo acme/x", MERGE + " --repo=acme/x", "gh pr merge -R o/r 7"):
            with self.subTest(cmd=cmd):
                self.assertBlocked(self.hook(cmd, cwd=self.repo), "-R/--repo")

    def test_uncommitted_change_after_gate_makes_hook_block(self):
        """Iz ishchi daraxt uchun yoziladi; commit qilinmagan o'zgarish PR head daraxtidan farq qiladi."""
        (self.sub / "02.md").write_text("commit qilinmagan")
        if not GATE_STAMP.is_file():
            self.skipTest("gate_stamp.py yo'q")
        r = subprocess.run([sys.executable, str(GATE_STAMP), "--write", "--config", str(self.cfg_path)], capture_output=True, text=True, env=self.env,
                           cwd=str(self.sub))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.review_stamp(repo=self.sub, tree=self.sub_tree, head=self.sub_head)
        self.assertBlocked(self.hook(MERGE, cwd=self.sub), "make ci boshqa kod uchun")

    def test_gate_stamp_script_output_is_accepted_by_the_hook(self):
        """gate_stamp.py va hook bir xil sozlamadan bir xil iz nomi va daraxt formatini ishlatadi."""
        if not GATE_STAMP.is_file():
            self.skipTest("gate_stamp.py yo'q")
        r = subprocess.run([sys.executable, str(GATE_STAMP), "--write", "--config", str(self.cfg_path)], capture_output=True, text=True, env=self.env,
                           cwd=str(self.sub))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(stamp_path(self.sub, "sos-ci-ok").read_text().split()[0], self.sub_tree)
        self.review_stamp(repo=self.sub, tree=self.sub_tree, head=self.sub_head)
        self.assertAllowed(self.hook(f"cd {SUB_REL} && {MERGE}", cwd=self.repo))


class ReviewerOnlyStampTests(HookTestCase):
    """Ikkinchi qoida: `review_stamp.py --head/--report` (izni yozadigan shakl) faqat reviewer agentidan; --check va --allow hammaga ochiq."""

    WRITE = "python3 .claude/quality/review_stamp.py --pr 7 --head " + "a" * 40 + " --report /tmp/report.md"

    def test_reviewer_agent_may_write_the_stamp(self):
        r, calls = self.run_hook(self.WRITE, agent="opus-reviewer")
        self.assertAllowed(r)
        self.assertEqual(calls, [])

    def test_other_agent_is_blocked(self):
        for agent in ("general-purpose", "Explore", "sonnet-coder", "opus-reviewer-2", "Opus-Reviewer", ""):
            with self.subTest(agent=agent):
                r, calls = self.run_hook(self.WRITE, agent=agent)
                self.assertBlocked(r, "ko'rik izini faqat opus-reviewer agenti yozadi")
                self.assertEqual(calls, [])

    def test_main_session_without_agent_type_is_blocked(self):
        r = self.hook(self.WRITE)
        self.assertBlocked(r, "ko'rik izini faqat opus-reviewer agenti yozadi")
        self.assertNotIn("agent_type", json.dumps({"tool_input": {"command": self.WRITE}}))
        r = self.hook(self.WRITE, event={"agent_type": None})
        self.assertBlocked(r, "ko'rik izini faqat opus-reviewer agenti yozadi")

    def test_check_and_allow_forms_are_free_for_everyone(self):
        cmds = ["python3 .claude/quality/review_stamp.py --pr 7 --check", "python3 .claude/quality/review_stamp.py --pr 7 --allow",
                "python3 .claude/quality/review_stamp.py --pr 7 --allow --round 3", "python3 review_stamp.py --check --pr 7",
                "./review_stamp.py --pr 7 --check", "cd docs/handbook && python3 ../../.claude/quality/review_stamp.py --pr 7 --check"]
        for agent in (None, "general-purpose", "opus-reviewer"):
            for cmd, (r, _) in zip(cmds, self.run_many(cmds, agent=agent)):
                with self.subTest(cmd=cmd, agent=agent):
                    self.assertAllowed(r)

    def test_invocation_forms_are_recognised(self):
        head = "a" * 40
        cmds = [f"python3 .claude/quality/review_stamp.py --pr 7 --head {head} --report r.md", f"python .claude/quality/review_stamp.py --head {head}",
                f"python3 -u /abs/path/review_stamp.py --report=r.md --pr 7 --head={head}", f"./review_stamp.py --pr 7 --head {head} --report r.md",
                ".claude/quality/review_stamp.py --pr 7 --report r.md", f"python3.11 ../../.claude/quality/review_stamp.py --pr 7 --head {head}",
                f"python3 -m review_stamp --pr 7 --head {head} --report r.md", f"python3 \"$ROOT/.claude/quality/review_stamp.py\" --pr 7 --head {head}",
                f"FOO=1 python3 review_stamp.py --head {head}", f"env PYTHONPATH=x python3 review_stamp.py --head {head}", "time python3 review_stamp.py --report r.md",
                f"cd docs/handbook && python3 ../../.claude/quality/review_stamp.py --pr 7 --head {head} --report r.md",
                f"echo go; python3 review_stamp.py --pr 7 --head {head}", f"python3 review_stamp.py --pr 7 --hea {head}", "python3 review_stamp.py --pr 7 --rep r.md",
                f"(python3 review_stamp.py --pr 7 --head {head})", f"true | python3 review_stamp.py --head {head}"]
        denied, allowed = self.run_many(cmds), self.run_many(cmds, agent="opus-reviewer")
        for cmd, (r, _), (ok, _) in zip(cmds, denied, allowed):
            with self.subTest(cmd=cmd):
                self.assertBlocked(r, "ko'rik izini faqat opus-reviewer agenti yozadi")
                self.assertAllowed(ok)

    def test_nested_forms_are_recognised(self):
        head = "a" * 40
        inner = f"python3 .claude/quality/review_stamp.py --pr 7 --head {head} --report r.md"
        cmds = [f"bash -c '{inner}'", f'sh -c "{inner}"', f"eval '{inner}'", f"echo $({inner})", f"x=`{inner}`", f'echo "$({inner})"',
                f"sudo bash -lc '{inner}'"]
        denied, allowed = self.run_many(cmds), self.run_many(cmds, agent="opus-reviewer")
        for cmd, (r, _), (ok, _) in zip(cmds, denied, allowed):
            with self.subTest(cmd=cmd):
                self.assertBlocked(r, "ko'rik izini faqat")
                self.assertAllowed(ok)

    def test_unbalanced_quotes_fall_back_to_text_check(self):
        cmd = f"python3 review_stamp.py --pr 7 --head {'a' * 40} --report 'r.md"
        self.assertBlocked(self.hook(cmd), "ko'rik izini faqat")
        self.assertAllowed(self.hook(cmd, agent="opus-reviewer"))

    def test_lookalikes_are_not_stamp_writes(self):
        cmds = ["cat .claude/quality/review_stamp.py", "grep -n head .claude/quality/review_stamp.py",
                "python3 -m unittest tests/test_review_stamp.py", "python3 my_review_stamp.py --head x", "python3 review_stamp2.py --head x",
                "git commit -m 'docs: review_stamp.py --head --report'", "echo 'run review_stamp.py with --head'", "python3 other.py --head x --report y",
                "python3 review_stamp.py --pr 7", "python3 review_stamp.py --help", "git add .claude/quality/review_stamp.py"]
        for cmd, (r, _) in zip(cmds, self.run_many(cmds)):
            with self.subTest(cmd=cmd):
                self.assertAllowed(r)

    def test_mentions_as_arguments_of_other_programs_pass(self):
        """Skript nomi bilan --head/--report bir buyruqda bo'lsa ham, skript dastur bo'lmasa (grep, cat, sed, echo, ls, git ...) bu oddiy matn: faqat o'qish bloklanmaydi."""
        cmds = ["grep -n -- --report .claude/quality/review_stamp.py", "grep -n -- --head .claude/quality/review_stamp.py", "grep -- --report review_stamp.py",
                "cat .claude/quality/review_stamp.py --head", "sed -n 1,20p .claude/quality/review_stamp.py --report", "echo review_stamp.py --head x --report y",
                "ls -l .claude/quality/review_stamp.py --report", "head -n 5 review_stamp.py --head", "diff review_stamp.py ../kit/review_stamp.py --report",
                "git diff main -- .claude/quality/review_stamp.py --head", "FOO=1 grep -- --report review_stamp.py", "sudo grep -- --head review_stamp.py",
                "time grep -c -- --report .claude/quality/review_stamp.py", "true && grep -- --report .claude/quality/review_stamp.py | head -n 3",
                "bash -c 'grep -n -- --report .claude/quality/review_stamp.py'", "eval 'cat review_stamp.py --head'", "echo $(grep -c -- --head review_stamp.py)",
                "x=`grep -c -- --report review_stamp.py`", "python3 -m pytest .claude/quality/review_stamp.py --head", "python3 other.py review_stamp.py --head",
                "python3 -c 'print(1)' review_stamp.py --head"]
        for agent in (None, "general-purpose", "opus-reviewer"):
            for cmd, (r, calls) in zip(cmds, self.run_many(cmds, agent=agent)):
                with self.subTest(cmd=cmd, agent=agent):
                    self.assertAllowed(r)
                    self.assertEqual(calls, [])

    def test_interpreter_options_before_the_script_are_skipped(self):
        head = "a" * 40
        cmds = [f"python3 -I -B -u .claude/quality/review_stamp.py --pr 7 --head {head}", f"python3 -Bu review_stamp.py --head {head}",
                f"python3 -W ignore review_stamp.py --head {head}", f"python3 -Wignore review_stamp.py --head {head}", f"python3 -X dev review_stamp.py --head {head}",
                f"python3 -- review_stamp.py --head {head}", f"python3 -mreview_stamp --head {head}", f"python3 -m quality.review_stamp --head {head}",
                "/usr/local/bin/python3.12 -B review_stamp.py --report r.md", f"python -OO -s review_stamp.py --pr 7 --head {head}"]
        denied, allowed = self.run_many(cmds), self.run_many(cmds, agent="opus-reviewer")
        for cmd, (r, _), (ok, _) in zip(cmds, denied, allowed):
            with self.subTest(cmd=cmd):
                self.assertBlocked(r, "ko'rik izini faqat opus-reviewer agenti yozadi")
                self.assertAllowed(ok)

    def test_rule_is_independent_of_merge_and_both_apply(self):
        """Merge bo'lmasa ham ishlaydi; bir buyruqda ikkalasi bo'lsa ham har biri tekshiriladi."""
        both = self.WRITE + " && gh pr merge 7 --merge"
        r, calls = self.run_hook(both, agent="opus-reviewer")
        self.assertBlocked(r, "make ci yashil natijasi yo'q")  # reviewer izni yoza oladi, merge esa baribir izlarsiz o'tmaydi
        self.assertBlocked(self.hook(both), "ko'rik izini faqat")

    def test_reviewer_name_comes_from_config(self):
        self.cfg_path.write_text(json.dumps({"prefix": "acme", "reviewer": "code-critic"}))
        self.assertAllowed(self.hook(self.WRITE, agent="code-critic"))
        r = self.hook(self.WRITE, agent="opus-reviewer")
        self.assertBlocked(r, "ko'rik izini faqat code-critic agenti yozadi")
        self.assertNotIn("opus-reviewer agenti", r.stderr)
        self.assertBlocked(self.hook(self.WRITE), "code-critic")

    def test_default_reviewer_without_config(self):
        env = dict(self.env, QUALITY_CONFIG=str(self.tmp / "yoq.json"))
        self.assertAllowed(self.hook(self.WRITE, agent="opus-reviewer", env=env))
        self.assertBlocked(self.hook(self.WRITE, agent="other", env=env), "opus-reviewer")


class GateStampWriteTests(HookTestCase):
    """Ikkinchi qoidaning darvoza izi qismi: `gate_stamp.py --write` (darvoza izini yozadigan shakl) hech kimga, reviewer ga ham ochiq emas: izni darvoza skripti yozadi,
    hook uning o'z chaqiruvini ko'rmaydi (asbob buyrug'i `make ci`). --tree, --check, --clear, --help ochiq. Skript faqat dastur sifatida ishga tushganda tekshiriladi."""

    WRITE = "python3 .claude/quality/gate_stamp.py --write"
    MSG = "darvoza izini faqat darvoza skripti yozadi (make ci ichida), qo'lda emas"

    def test_write_is_blocked_for_everyone_including_the_reviewer(self):
        for agent in (None, "general-purpose", "opus-reviewer"):
            with self.subTest(agent=agent):
                r, calls = self.run_hook(self.WRITE, agent=agent)
                self.assertBlocked(r, self.MSG)
                self.assertIn("--tree, --check, --clear", r.stderr)
                self.assertEqual(calls, [])

    def test_invocation_forms_are_recognised(self):
        tree = "a" * 40
        cmds = [f"{self.WRITE} --tree {tree}", f"python3 .claude/quality/gate_stamp.py --tree {tree} --write", "python3 -B .claude/quality/gate_stamp.py --write",
                "python3 -I -B -u .claude/quality/gate_stamp.py --write", "python3 -Bu gate_stamp.py --write", "python3 -W ignore gate_stamp.py --write",
                "python3 -- gate_stamp.py --write", "python .claude/quality/gate_stamp.py --write", "python3.11 ../../.claude/quality/gate_stamp.py --write",
                "/usr/bin/python3 /abs/path/gate_stamp.py --write", "./gate_stamp.py --write", f".claude/quality/gate_stamp.py --write --tree {tree}",
                "python3 -m gate_stamp --write", "python3 -mgate_stamp --write", 'python3 "$ROOT/.claude/quality/gate_stamp.py" --write',
                "FOO=1 python3 gate_stamp.py --write", "env PYTHONPATH=x python3 gate_stamp.py --write", "time python3 gate_stamp.py --write",
                "nohup python3 gate_stamp.py --write", "sudo -u x python3 gate_stamp.py --write", "timeout 30 python3 gate_stamp.py --write",
                "cd docs/handbook && python3 ../../.claude/quality/gate_stamp.py --write", "echo go; python3 gate_stamp.py --write", "(python3 gate_stamp.py --write)",
                "true | python3 gate_stamp.py --write", "python3 gate_stamp.py --write > out.log 2>&1", "python3 gate_stamp.py --write=1",
                "python3 gate_stamp.py --wr", "python3 gate_stamp.py --writ", "python3 gate_stamp.py --w", "python3 gate_stamp.py --clear --write"]
        for cmd, (r, calls) in zip(cmds, self.run_many(cmds)):
            with self.subTest(cmd=cmd):
                self.assertBlocked(r, self.MSG)
                self.assertEqual(calls, [])

    def test_nested_forms_are_recognised(self):
        inner = self.WRITE + " --tree abc"
        cmds = [f"bash -c '{inner}'", f'sh -c "{inner}"', f"eval '{inner}'", f"echo $({inner})", f"x=`{inner}`", f'echo "$({inner})"', f"sudo bash -lc '{inner}'",
                f'env FOO=1 sh -c "{inner}"', f"tree=$({inner})"]
        for cmd, (r, _) in zip(cmds, self.run_many(cmds)):
            with self.subTest(cmd=cmd):
                self.assertBlocked(r, self.MSG)

    def test_read_only_forms_stay_allowed(self):
        cmds = ["python3 .claude/quality/gate_stamp.py --tree", "python3 -B .claude/quality/gate_stamp.py --tree", "python3 -I -B -u .claude/quality/gate_stamp.py --tree",
                "python3 .claude/quality/gate_stamp.py --check", "python3 .claude/quality/gate_stamp.py --check --head HEAD", "python3 .claude/quality/gate_stamp.py --clear",
                "python3 .claude/quality/gate_stamp.py --help", "python3 .claude/quality/gate_stamp.py -h", "python3 .claude/quality/gate_stamp.py", "./gate_stamp.py --tree",
                "tree=$(python3 .claude/quality/gate_stamp.py --tree)", "python3 .claude/quality/gate_stamp.py --repo /srv/writer --tree",
                "python3 .claude/quality/gate_stamp.py --config w.json --check", "bash -c 'python3 .claude/quality/gate_stamp.py --clear'",
                "python3 -m gate_stamp --tree", "FOO=1 python3 gate_stamp.py --check", "cd docs/handbook && python3 ../../.claude/quality/gate_stamp.py --tree"]
        for agent in (None, "opus-reviewer"):
            for cmd, (r, calls) in zip(cmds, self.run_many(cmds, agent=agent)):
                with self.subTest(cmd=cmd, agent=agent):
                    self.assertAllowed(r)
                    self.assertEqual(calls, [])

    def test_mentions_as_arguments_of_other_programs_pass(self):
        cmds = ["cat .claude/quality/gate_stamp.py", "grep -n -- --write .claude/quality/gate_stamp.py", "grep --write gate_stamp.py", "sed -n 1,5p gate_stamp.py --write",
                "echo gate_stamp.py --write", "git commit -m 'docs: gate_stamp.py --write only inside the gate'", "git add .claude/quality/gate_stamp.py",
                "ls gate_stamp.py --write", "python3 my_gate_stamp.py --write", "python3 gate_stamp2.py --write", "python3 other.py gate_stamp.py --write",
                "python3 -m unittest tests/test_gate_stamp.py", "python3 -m py_compile .claude/quality/gate_stamp.py", "python3 other.py --write",
                "bash -c 'grep -n -- --write .claude/quality/gate_stamp.py'", "echo $(grep -c -- --write gate_stamp.py)", "head -n 3 gate_stamp.py --write",
                "python3 -c 'print(1)' gate_stamp.py --write"]
        for cmd, (r, calls) in zip(cmds, self.run_many(cmds)):
            with self.subTest(cmd=cmd):
                self.assertAllowed(r)
                self.assertEqual(calls, [])

    def test_gate_command_comes_from_config(self):
        self.cfg_path.write_text(json.dumps({"prefix": "acme", "gate_command": "just gate"}))
        r = self.hook(self.WRITE)
        self.assertBlocked(r, "darvoza izini faqat darvoza skripti yozadi (just gate ichida), qo'lda emas")
        self.assertNotIn("make ci", r.stderr)
        env = dict(self.env, QUALITY_CONFIG=str(self.tmp / "yoq.json"))
        self.assertBlocked(self.hook(self.WRITE, env=env), "(make ci ichida)")

    def test_rule_is_independent_of_merge_and_both_apply(self):
        """Merge o'tadigan holatda ham izni qo'lda yozish bloklanadi (qaysi tartibda bo'lmasin); merge izlarsiz bo'lsa uni ham bloklaydi."""
        self.ci_stamp()
        self.review_stamp()
        self.assertAllowed(self.hook(MERGE))
        self.assertBlocked(self.hook(f"{MERGE} && {self.WRITE}"), self.MSG)
        self.assertBlocked(self.hook(f"{self.WRITE} && {MERGE}"), self.MSG)
        self.stamp_path_ci().unlink()
        self.assertBlocked(self.hook(f"{MERGE} && {self.WRITE}"), "make ci yashil natijasi yo'q")

    def test_unbalanced_quotes_fall_back_to_text_check(self):
        self.assertBlocked(self.hook(f"{self.WRITE} 'x"), self.MSG)
        self.assertAllowed(self.hook("python3 .claude/quality/gate_stamp.py --tree 'x"))

    def test_the_gate_scripts_own_call_is_invisible_to_the_hook(self):
        """Hook asbob buyrug'ini (`make ci`, `sh gate.sh`) ko'radi, skript ichidagi gate_stamp.py --write ni emas: darvoza haqiqiy jarayonda izni yozadi va merge o'tadi."""
        if not GATE_STAMP.is_file():
            self.skipTest("gate_stamp.py yo'q")
        script = self.tmp / "gate.sh"
        script.write_text(f'#!/bin/sh\ntree=$("{sys.executable}" "{GATE_STAMP}" --tree) || exit 1\n"{sys.executable}" "{GATE_STAMP}" --write --tree "$tree"\n')
        script.chmod(0o755)
        for cmd in ("make ci", f"sh {script}", f"bash {script}", str(script)):
            with self.subTest(cmd=cmd):
                self.assertAllowed(self.hook(cmd))
        r = subprocess.run(["sh", str(script)], capture_output=True, text=True, env=self.env, cwd=str(self.repo))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.stamp_path_ci().read_text().split()[0], self.tree)
        self.review_stamp()
        self.assertAllowed(self.hook(MERGE))

    def stamp_path_ci(self):
        return stamp_path(self.repo, self.ci_name)


class ConfigTests(HookTestCase):
    """Sozlama: iz nomlari prefix dan, xabarlar gate_command va reviewer dan; buzuq/yo'q sozlama standartga qaytadi."""

    def test_sos_prefix_gives_sos_scrum_stamp_names(self):
        self.assertEqual(SOS["prefix"] + "-ci-ok", "sos-ci-ok")
        self.assertEqual(Path(str(stamp_path(self.repo, self.ci_name))).name, "sos-ci-ok")
        self.assertEqual(Path(str(self.review_path(7))).name, "sos-review-ok-7")
        self.ci_stamp()
        self.review_stamp()
        self.assertAllowed(self.hook(MERGE))

    def test_default_prefix_is_quality_and_sos_stamps_are_not_used(self):
        self.cfg_path.write_text("{}")
        self.ci_stamp()  # sos-ci-ok
        self.review_stamp()
        r = self.hook(MERGE)
        self.assertBlocked(r, "make ci yashil natijasi yo'q")
        self.assertIn("quality-ci-ok", r.stderr)
        stamp_path(self.repo, "quality-ci-ok").write_text(f"{self.tree} 2026-10-24T10:00:00Z\n")
        r = self.hook(MERGE)
        self.assertBlocked(r, "mustaqil ko'rik izi yo'q yoki buzuq")
        self.assertIn("quality-review-ok-7", r.stderr)
        stamp_path(self.repo, "quality-review-ok-7").write_text(f"{self.tree} 7 {self.head} 2026-10-24T10:05:00Z round=1 blockers=0 major=0 minor=0\n")
        self.assertAllowed(self.hook(MERGE))

    def test_custom_prefix_gate_command_and_reviewer_appear_in_messages(self):
        self.cfg_path.write_text(json.dumps({"prefix": "acme", "gate_command": "just verify", "reviewer": "code-critic"}))
        r = self.hook(MERGE)
        self.assertBlocked(r, "just verify yashil natijasi yo'q")
        self.assertIn("acme-ci-ok", r.stderr)
        self.assertNotIn("make ci", r.stderr)
        stamp_path(self.repo, "acme-ci-ok").write_text("0" * 40 + " t\n")
        self.assertBlocked(self.hook(MERGE), "just verify boshqa kod uchun")
        stamp_path(self.repo, "acme-ci-ok").write_text(f"{self.tree} t\n")
        r = self.hook(MERGE)
        self.assertBlocked(r, "mustaqil ko'rik izi yo'q yoki buzuq")
        self.assertIn("acme-review-ok-7", r.stderr)
        self.assertIn("code-critic agenti PR #7", r.stderr)
        self.assertNotIn("opus-reviewer", r.stderr)
        stamp_path(self.repo, "acme-review-ok-7").write_text(f"{self.tree} 7 {self.head} t round=1 blockers=1 major=0 minor=0\n")
        r = self.hook(MERGE)
        self.assertBlocked(r, "bloker bor")
        self.assertIn("code-critic", r.stderr)
        self.set_pr(self.repo, 7, "f" * 40)
        self.assertBlocked(self.hook(MERGE), "git fetch, keyin just verify")

    def test_broken_config_falls_back_to_defaults_with_one_warning(self):
        self.cfg_path.write_text("{ bu JSON emas")
        r = self.hook(MERGE)
        self.assertEqual(r.returncode, 2, r.stderr)
        lines = r.stderr.splitlines()
        self.assertEqual(len(lines), 2, r.stderr)
        self.assertTrue(lines[0].startswith("ogohlantirish: "), lines[0])
        self.assertIn("standart sozlama ishlatiladi", lines[0])
        self.assertTrue(lines[1].startswith("Merge bloklandi: "), lines[1])
        self.assertIn("quality-ci-ok", lines[1])

    def test_invalid_values_fall_back_per_key_with_warning(self):
        self.cfg_path.write_text(json.dumps({"prefix": "a/b", "gate_command": "", "reviewer": 5}))
        r = self.hook(MERGE)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertEqual(len([ln for ln in r.stderr.splitlines() if ln.startswith("ogohlantirish: ")]), 3, r.stderr)
        self.assertIn("quality-ci-ok", r.stderr)
        self.assertIn("make ci yashil natijasi yo'q", r.stderr)

    def test_non_object_config_falls_back_to_defaults(self):
        for body in ("[]", "5", "null", '"sos"'):
            with self.subTest(body=body):
                self.cfg_path.write_text(body)
                r = self.hook(MERGE)
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertIn("quality-ci-ok", r.stderr)

    def test_missing_config_file_is_silent_defaults(self):
        env = dict(self.env, QUALITY_CONFIG=str(self.tmp / "yoq.json"))
        r = self.hook(MERGE, env=env)
        self.assertBlocked(r, "make ci yashil natijasi yo'q")
        self.assertIn("quality-ci-ok", r.stderr)

    def test_config_argument_beats_environment(self):
        other = self.tmp / "other.json"
        other.write_text(json.dumps({"prefix": "viaarg"}))
        payload = json.dumps({"tool_input": {"command": MERGE}, "cwd": str(self.repo)})
        r = subprocess.run([sys.executable, str(HOOK), "--config", str(other)], input=payload, capture_output=True, text=True, env=self.env, cwd=str(self.tmp))
        self.assertBlocked(r, "viaarg-ci-ok")

    def test_unknown_arguments_never_block(self):
        r = subprocess.run([sys.executable, str(HOOK), "--bilinmaydi", "x"], input=json.dumps({"tool_input": {"command": "ls"}}), capture_output=True,
                           text=True, env=self.env, cwd=str(self.tmp))
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_default_config_location_is_next_to_hook(self):
        """Muhit o'zgaruvchisiz: <hook papkasi>/../quality/config.json. Hook nusxasi vaqtinchalik loyiha ichida."""
        project = self.tmp / "project"
        (project / ".claude" / "hooks").mkdir(parents=True)
        (project / ".claude" / "quality").mkdir(parents=True)
        hook_copy = project / ".claude" / "hooks" / "merge-guard.py"
        shutil.copyfile(HOOK, hook_copy)
        (project / ".claude" / "quality" / "config.json").write_text(json.dumps({"prefix": "nextto", "gate_command": "run gate"}))
        env = {k: v for k, v in self.env.items() if k != "QUALITY_CONFIG"}
        payload = json.dumps({"tool_input": {"command": MERGE}, "cwd": str(self.repo)})
        r = subprocess.run([sys.executable, str(hook_copy)], input=payload, capture_output=True, text=True, env=env, cwd=str(self.tmp))
        self.assertBlocked(r, "run gate yashil natijasi yo'q")
        self.assertIn("nextto-ci-ok", r.stderr)
        r = subprocess.run([sys.executable, str(hook_copy)], input=payload, capture_output=True, text=True, env=dict(env, QUALITY_CONFIG=str(self.cfg_path)),
                           cwd=str(self.tmp))
        self.assertIn("sos-ci-ok", r.stderr)  # QUALITY_CONFIG hook yonidagi sozlamadan ustun

    def test_no_project_specific_names_in_hook_source(self):
        text = HOOK.read_text().lower()
        for needle in ("sos-", "sos_", "manba", "arxitektura", "holat.md", "sifat-jurnali", "laravel", "angular"):
            self.assertNotIn(needle, text, needle)


class PerformanceTests(HookTestCase):
    """Katta buyruq (20 KB) bir soniyadan ancha tez javob beradi; patologik kiritish ham qotmaydi va yiqilmaydi."""

    LIMIT = 1.0

    def timed(self, cmd, **kw):
        t0 = time.monotonic()
        r = self.hook(cmd, **kw)
        return r, time.monotonic() - t0

    def test_20kb_merge_command_is_fast(self):
        cmd = "FOO=1 " * 3400 + "gh pr merge 8 --merge"
        self.assertGreater(len(cmd), 20000)
        self.set_pr(self.repo, 8, self.head)
        r, took = self.timed(cmd)
        self.assertBlocked(r, "make ci yashil natijasi yo'q")
        self.assertLess(took, self.LIMIT)

    def test_20kb_merge_with_long_quoted_body_is_fast(self):
        cmd = "gh pr merge 7 --merge --body '" + "gh pr merge 7; " * 1400 + "'"
        self.assertGreater(len(cmd), 20000)
        r, took = self.timed(cmd)
        self.assertBlocked(r, "make ci yashil natijasi yo'q")
        self.assertLess(took, self.LIMIT)

    def test_20kb_of_commands_without_merge_is_fast(self):
        cmd = ";".join(["gh pr view 1"] * 1700)
        self.assertGreater(len(cmd), 20000)
        r, took = self.timed(cmd)
        self.assertAllowed(r)
        self.assertLess(took, self.LIMIT)

    def test_20kb_non_gh_command_is_fast(self):
        r, took = self.timed("echo " + "x" * 20000)
        self.assertAllowed(r)
        self.assertLess(took, self.LIMIT)

    def test_20kb_heredoc_with_merge_text_is_fast_and_allowed(self):
        cmd = "gh pr create --body-file - <<'EOF'\n" + "gh pr merge 7 --merge\n" * 1000 + "EOF"
        self.assertGreater(len(cmd), 20000)
        r, took = self.timed(cmd)
        self.assertAllowed(r)
        self.assertLess(took, self.LIMIT)

    def test_many_unterminated_heredocs_are_fast(self):
        cmd = "cat <<A\n" * 2500 + "gh pr view 1"
        self.assertGreater(len(cmd), 20000)
        r, took = self.timed(cmd)
        self.assertAllowed(r)
        self.assertLess(took, self.LIMIT)

    def test_20kb_of_gh_flags_is_fast(self):
        for cmd in ("gh pr " + "-x " * 7000 + "merge 'unterminated", "gh " + "-x y " * 5000 + "pr merge 'x", "gh pr " + "-x " * 7000 + "'unterminated",
                    "gh pr -x " * 2500 + "'x"):
            with self.subTest(cmd=cmd[:40]):
                self.assertGreater(len(cmd), 20000)
                r, took = self.timed(cmd)
                if "merge" in cmd:
                    self.assertBlocked(r, "tirnoqlar juftlanmagan")
                else:
                    self.assertAllowed(r)
                self.assertLess(took, self.LIMIT)

    def test_many_valid_merges_run_the_checks_once(self):
        self.ci_stamp()
        self.review_stamp()
        r, calls = self.run_hook(";".join(["gh pr merge 7 --merge"] * 800))
        self.assertAllowed(r)
        self.assertEqual(calls, ["repo: pr view 7 --json number,headRefOid"])

    def test_deeply_nested_substitutions_do_not_crash(self):
        for depth in (30, 3000):
            with self.subTest(depth=depth):
                r = self.hook("gh pr merge 7 " + "$(" * depth + "x" + ")" * depth)
                self.assertBlocked(r)
                self.assertNotIn("Traceback", r.stderr)
        r = self.hook("echo " + "$(" * 3000 + "x" + ")" * 3000 + " gh")
        self.assertNotEqual(r.returncode, 2, r.stderr)


if __name__ == "__main__":
    unittest.main()
