#!/usr/bin/env python3
"""gate_stamp.py testlari: ishchi daraxt xeshi (--tree), izni yozish/o'chirish/tekshirish (--write, --clear, --check), iz nomlari sozlamadan.

Qora quti: skript subprocess orqali, hamma git repo vaqtinchalik papkada (haqiqiy repo va ~/.claude ga tegilmaydi). Xeshning to'g'riligi git ning
o'z hisobi bilan solishtiriladi: skript xeshi chiqargach o'sha holat `git add -A && git commit` qilinadi va commit daraxti taqqoslanadi.
Ikki joylashuvni biladi: to'plam papkasi (gate_stamp.py yonma-yon) va o'rnatilgan loyiha (.claude/quality/gate_stamp.py, .claude/quality/tests).

  python3 -m unittest discover -s .claude/quality/tests -p 'test_gate_stamp.py' -v
"""
import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

QUALITY = Path(__file__).resolve().parent.parent  # to'plam papkasi yoki <loyiha>/.claude/quality
SCRIPT = QUALITY / "gate_stamp.py"

HOME = tempfile.mkdtemp(prefix="gate-stamp-home-")
BASE_ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_") and k not in ("CLAUDE_PROJECT_DIR", "QUALITY_CONFIG")}
BASE_ENV.update(
    HOME=HOME, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1",
    GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com",
)
SEQ = itertools.count()
STAMP_LINE = re.compile(r"^([0-9a-f]{40}) (\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ)\n$")
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
OLD_MTIME = 1_000_000_000  # 2001-09-09: indeks nusxasining yangi mtime idan ancha oldin


def tearDownModule():
    shutil.rmtree(HOME, ignore_errors=True)


def git(cwd, *args, check=True, env=None):
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, env=env or BASE_ENV)
    if check and r.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} ({cwd}): {r.stderr}")
    return r.stdout.strip()


def commit_all(repo, message="all"):
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--allow-empty", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def write(repo, name, text="x\n"):
    p = Path(repo) / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


class GateTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gate-stamp-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.tmpdir = self.tmp / "tmpdir"  # skriptning vaqtinchalik fayllari shu yerga tushadi: keyin bo'sh bo'lishi shart
        self.tmpdir.mkdir()
        self.cfg = self.tmp / "config.json"
        self.cfg.write_text(json.dumps({"prefix": "sos"}))
        self.env = dict(BASE_ENV, TMPDIR=str(self.tmpdir), QUALITY_CONFIG=str(self.cfg))
        self.repo = self.tmp / "repo"
        git(self.tmp, "init", "-q", "-b", "main", str(self.repo))
        write(self.repo, "base.txt", "base\n")
        write(self.repo, "src/app.txt", "app\n")
        commit_all(self.repo, "base")
        self.git_dir = self.repo / ".git"

    def run_gate(self, *args, cwd=None, env=None):
        return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, env=env or self.env, cwd=str(cwd or self.repo))

    def tree(self, **kw):
        r = self.run_gate("--tree", **kw)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr, "")
        return r.stdout.strip()

    def stamp_file(self, name="sos-ci-ok", repo=None):
        return Path(repo or self.repo) / git(repo or self.repo, "rev-parse", "--git-path", name)

    def head_tree(self, repo=None):
        return git(repo or self.repo, "rev-parse", "HEAD^{tree}")


class TreeHashTests(GateTestCase):
    def test_tree_of_clean_checkout_equals_head_tree(self):
        self.assertEqual(self.tree(), self.head_tree())

    def test_tree_equals_what_git_commits_for_tracked_untracked_modified_and_deleted(self):
        write(self.repo, "base.txt", "o'zgargan\n")  # kuzatilgan, o'zgargan
        write(self.repo, "new/untracked.txt", "yangi\n")  # kuzatilmagan
        write(self.repo, "staged.txt", "staged\n")
        git(self.repo, "add", "staged.txt")  # indeksda
        (self.repo / "src" / "app.txt").unlink()  # o'chirilgan
        expected = self.tree()
        self.assertNotEqual(expected, self.head_tree())
        commit_all(self.repo)
        self.assertEqual(expected, self.head_tree())

    def test_tree_honours_gitignore_and_untracked_files_change_it(self):
        write(self.repo, ".gitignore", "*.log\nbuild/\n")
        commit_all(self.repo)
        before = self.tree()
        write(self.repo, "debug.log", "shovqin\n")
        write(self.repo, "build/out.bin", "chiqish\n")
        write(self.repo, "src/deep.log", "shovqin\n")
        self.assertEqual(self.tree(), before)
        write(self.repo, "notes.txt", "yangi fayl\n")
        self.assertNotEqual(self.tree(), before)

    def test_tracked_file_that_is_now_ignored_stays_in_the_tree(self):
        """Kuzatilgan fayl keyin .gitignore ga tushsa ham HEAD daraxtida qoladi va uning o'zgarishi hisobga olinadi."""
        write(self.repo, "kept.cfg", "1\n")
        commit_all(self.repo)
        write(self.repo, ".gitignore", "*.cfg\n")
        commit_all(self.repo)
        clean = self.tree()
        self.assertEqual(clean, self.head_tree())
        write(self.repo, "kept.cfg", "2\n")
        changed = self.tree()
        self.assertNotEqual(changed, clean)
        commit_all(self.repo)
        self.assertEqual(changed, self.head_tree())

    # --- eskirgan indeks nusxasi: stat mos, mazmun boshqa
    # Nuqson: --tree haqiqiy indeksni vaqtinchalik faylga nusxalaydi, nusxa YANGI mtime oladi. Git yozuvni "poygali toza" deb faqat yozuv mtime i indeks mtime idan
    # kichik emas bo'lsa qayta xeshlaydi; nusxada hamma yozuv eski ko'rinadi, shuning uchun stat (mtime soniyasi, hajm) mos kelgan faylning mazmuni o'qilmasdi. Bir soniyada
    # hajmi teng qayta yozilgan kuzatilgan fayl (yuklama ostida `test_tracked_file_that_is_now_ignored_stays_in_the_tree` bir marta shundan yiqildi) eski xeshda qolardi.
    # Tuzatish: nusxaning mtime i 1 s ga qo'yiladi (0 emas: git sec=0 da poyga tekshiruvini o'chiradi). Quyida stat atayin mos qilinadi, shuning uchun test vaqtga bog'liq emas.

    def scratch_tree(self):
        """Kutilgan xesh: bo'sh vaqtinchalik indeksga `git add -A` (hamma fayl noldan xeshlanadi); fixture da hamma fayl kuzatilgan va .gitignore siz."""
        env = dict(BASE_ENV, GIT_INDEX_FILE=str(self.tmp / "scratch-index"))
        git(self.repo, "add", "-A", env=env)
        return git(self.repo, "write-tree", env=env)

    def plain_copy_tree(self):
        """Eski --tree: haqiqiy indeksning oddiy nusxasi (yangi mtime) da `git add -A`."""
        copy = self.tmp / "plain-index"
        shutil.copyfile(self.git_dir / "index", copy)
        env = dict(BASE_ENV, GIT_INDEX_FILE=str(copy))
        git(self.repo, "add", "-A", env=env)
        return git(self.repo, "write-tree", env=env)

    def rewrite_with_matching_stat(self):
        """victim.txt eski mtime bilan commit qilinadi, so'ng hajmi va mtime bir xil, joyida (inode o'zgarmaydi) boshqa mazmun bilan qayta yoziladi."""
        git(self.repo, "config", "core.trustctime", "false")  # qayta yozish ctime ni o'zgartiradi; mtime va hajm mosligi yetarli bo'lsin
        git(self.repo, "config", "core.checkStat", "minimal")  # mtime ning soniyasi va hajm; USE_NSEC bilan yig'ilgan git ham xuddi shunday
        victim = write(self.repo, "victim.txt", "aaaa\n")
        os.utime(victim, (OLD_MTIME, OLD_MTIME))
        commit_all(self.repo, "victim")
        before = self.head_tree()
        write(self.repo, "victim.txt", "bbbb\n")
        os.utime(victim, (OLD_MTIME, OLD_MTIME))
        return before

    def test_tracked_file_rewritten_with_matching_stat_is_still_rehashed(self):
        before = self.rewrite_with_matching_stat()
        expected = self.scratch_tree()
        self.assertNotEqual(expected, before)
        if self.plain_copy_tree() == expected:
            self.skipTest("bu git indeks nusxasidagi mos stat li yozuvni ham qayta xeshlaydi: nuqson bu yerda takrorlanmaydi")
        index = self.git_dir / "index"
        for mtime in (None, OLD_MTIME):  # haqiqiy indeks mtime i hozirgi, yoki yozuvnikidek eski (yuklamadagi nosozlik shu: yozuv va indeks bir soniyada)
            if mtime is not None:
                os.utime(index, (mtime, mtime))
            with self.subTest(real_index_mtime=mtime):
                self.assertEqual(self.tree(), expected)
        commit_all(self.repo)
        self.assertEqual(expected, self.head_tree())

    def test_write_refuses_when_a_file_was_rewritten_with_matching_stat_during_the_run(self):
        """Darvoza oqimi: --tree boshida olinadi, yurish davomida fayl (stat mos) o'zgaradi; --write --tree eski xeshni qabul qilmasligi shart."""
        started = self.rewrite_with_matching_stat()
        if self.plain_copy_tree() == self.scratch_tree():
            self.skipTest("bu git indeks nusxasidagi mos stat li yozuvni ham qayta xeshlaydi: nuqson bu yerda takrorlanmaydi")
        r = self.run_gate("--write", "--tree", started)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("fayllar o'zgardi", r.stderr)
        self.assertFalse(self.stamp_file().exists())

    def test_tree_ignores_the_state_of_the_real_index(self):
        """Indeksdagi eskirgan/yarim holat natijaga ta'sir qilmaydi va indeks fayli o'zgarmaydi."""
        write(self.repo, "a.txt", "1\n")
        git(self.repo, "add", "a.txt")
        write(self.repo, "a.txt", "2\n")  # indeksda eski, ishchi daraxtda yangi
        git(self.repo, "rm", "--cached", "-q", "base.txt")  # indeksdan o'chirilgan, diskda bor
        write(self.repo, "src/app.txt", "indeksdan keyin o'zgardi\n")
        index = self.git_dir / "index"
        before_bytes, before_status = index.read_bytes(), git(self.repo, "status", "--porcelain=v1")
        expected = self.tree()
        self.assertEqual(index.read_bytes(), before_bytes)
        self.assertEqual(git(self.repo, "status", "--porcelain=v1"), before_status)
        git(self.repo, "add", "-A")
        self.assertEqual(expected, git(self.repo, "write-tree"))

    def test_tree_leaves_no_temporary_files_and_never_touches_the_index(self):
        index = self.git_dir / "index"
        names_before, stat_before, bytes_before = sorted(os.listdir(self.git_dir)), os.stat(index).st_mtime_ns, index.read_bytes()
        write(self.repo, "x.txt")
        self.tree()
        self.assertEqual(list(self.tmpdir.iterdir()), [])
        self.assertEqual(sorted(os.listdir(self.git_dir)), names_before)
        self.assertEqual((os.stat(index).st_mtime_ns, index.read_bytes()), (stat_before, bytes_before))
        self.run_gate("--write")
        self.assertEqual(list(self.tmpdir.iterdir()), [])
        self.assertEqual(index.read_bytes(), bytes_before)

    def test_tree_is_the_same_from_a_subdirectory_and_with_repo_option(self):
        write(self.repo, "new.txt")
        top = self.tree()
        self.assertEqual(self.tree(cwd=self.repo / "src"), top)
        r = self.run_gate("--tree", "--repo", str(self.repo), cwd=self.tmp)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, top))
        r = self.run_gate("--tree", "--repo", str(self.repo / "src"), cwd=self.tmp)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, top))

    def test_tree_in_a_repo_without_commits_and_without_index(self):
        fresh = self.tmp / "fresh"
        git(self.tmp, "init", "-q", "-b", "main", str(fresh))
        self.assertEqual(self.tree(cwd=fresh), EMPTY_TREE)
        write(fresh, "a.txt")
        expected = self.tree(cwd=fresh)
        self.assertNotEqual(expected, EMPTY_TREE)
        commit_all(fresh)
        self.assertEqual(expected, self.head_tree(fresh))

    def test_tree_ignores_git_environment_variables(self):
        """GIT_DIR / GIT_INDEX_FILE (masalan git hook ichidan) joriy repo ni almashtirmaydi."""
        other = self.tmp / "other"
        git(self.tmp, "init", "-q", "-b", "main", str(other))
        write(other, "boshqa.txt")
        expected = self.tree()
        env = dict(self.env, GIT_DIR=str(other / ".git"), GIT_WORK_TREE=str(other), GIT_INDEX_FILE=str(self.tmp / "soxta-indeks"))
        self.assertEqual(self.tree(env=env), expected)

    def test_tree_is_deterministic_and_content_sensitive(self):
        write(self.repo, "a.txt", "1\n")
        first = self.tree()
        self.assertEqual(self.tree(), first)
        write(self.repo, "a.txt", "2\n")
        self.assertNotEqual(self.tree(), first)

    def test_tree_outside_a_repo_fails_clearly(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        r = self.run_gate("--tree", cwd=outside)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout, "")
        self.assertIn("git repo emas", r.stderr)

    @unittest.skipIf(os.geteuid() == 0, "root uchun ruxsat cheklovi ishlamaydi")
    def test_unreadable_file_makes_tree_fail_instead_of_printing_a_wrong_hash(self):
        secret = write(self.repo, "secret.txt")
        secret.chmod(0)
        self.addCleanup(secret.chmod, 0o644)
        r = self.run_gate("--tree")
        self.assertEqual((r.returncode, r.stdout), (1, ""))
        self.assertIn("git add -A muvaffaqiyatsiz", r.stderr)
        self.assertEqual(list(self.tmpdir.iterdir()), [])


class WriteClearTests(GateTestCase):
    def test_write_creates_the_stamp_with_tree_and_utc_time(self):
        r = self.run_gate("--write")
        self.assertEqual(r.returncode, 0, r.stderr)
        stamp = self.stamp_file()
        self.assertEqual(stamp.parent, self.git_dir)
        m = STAMP_LINE.match(stamp.read_text())
        self.assertIsNotNone(m, stamp.read_text())
        self.assertEqual(m.group(1), self.head_tree())
        when = datetime.strptime(m.group(2), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        self.assertLess(abs(datetime.now(timezone.utc) - when), timedelta(minutes=1))
        self.assertIn(m.group(1), r.stdout)
        self.assertEqual(list(self.tmpdir.iterdir()), [])
        self.assertEqual([n for n in os.listdir(self.git_dir) if n.endswith(".tmp")], [])  # tmp + os.replace: yarim fayl qolmaydi

    def test_write_with_matching_tree_writes(self):
        write(self.repo, "new.txt")
        tree = self.tree()
        r = self.run_gate("--write", "--tree", tree)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.stamp_file().read_text().split()[0], tree)

    def test_write_refuses_when_files_changed_since_the_tree_was_taken_and_removes_the_old_stamp(self):
        old = self.stamp_file()
        old.write_text(f"{'0' * 40} 2026-01-01T00:00:00Z\n")
        tree = self.tree()
        write(self.repo, "yurish-davomida.txt")  # darvoza yurishi davomida fayl o'zgardi
        r = self.run_gate("--write", "--tree", tree)
        self.assertEqual(r.returncode, 1)
        self.assertIn("darvoza izi yozilmadi", r.stderr)
        self.assertIn("o'zgardi", r.stderr)
        self.assertIn(tree, r.stderr)
        self.assertFalse(old.exists())
        self.assertEqual(r.stdout, "")
        self.assertEqual(list(self.tmpdir.iterdir()), [])

    def test_write_refuses_a_tree_that_is_not_a_hash_and_removes_the_old_stamp(self):
        for bad in ("", "abc", "XYZ" * 14, self.head_tree().upper(), self.head_tree() + "0"):
            with self.subTest(tree=bad):
                old = self.stamp_file()
                old.write_text(f"{self.head_tree()} 2026-01-01T00:00:00Z\n")
                r = self.run_gate("--write", "--tree", bad)
                self.assertEqual(r.returncode, 1, r.stderr)
                self.assertIn("daraxt xeshi emas", r.stderr)
                self.assertFalse(old.exists())

    def test_write_replaces_an_old_stamp(self):
        old = self.stamp_file()
        old.write_text(f"{'0' * 40} 2026-01-01T00:00:00Z\n")
        self.assertEqual(self.run_gate("--write").returncode, 0)
        self.assertEqual(self.stamp_file().read_text().split()[0], self.head_tree())

    def test_write_with_bare_tree_option_is_a_usage_error(self):
        old = self.stamp_file()
        old.write_text(f"{self.head_tree()} 2026-01-01T00:00:00Z\n")
        r = self.run_gate("--write", "--tree")
        self.assertEqual(r.returncode, 2)
        self.assertIn("--write --tree HASH", r.stderr)
        self.assertTrue(old.exists())  # noto'g'ri chaqiruv izga tegmaydi

    def test_clear_removes_the_stamp_and_is_idempotent(self):
        self.assertEqual(self.run_gate("--write").returncode, 0)
        self.assertTrue(self.stamp_file().exists())
        r = self.run_gate("--clear")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.stamp_file().exists())
        r = self.run_gate("--clear")
        self.assertEqual((r.returncode, r.stderr), (0, ""))

    def test_clear_only_touches_this_prefix(self):
        other = self.stamp_file("quality-ci-ok")
        other.write_text("x\n")
        self.assertEqual(self.run_gate("--write").returncode, 0)
        self.assertEqual(self.run_gate("--clear").returncode, 0)
        self.assertTrue(other.exists())

    def test_gate_flow_green_and_red_like_a_gate_script(self):
        """Darvoza skriptining haqiqiy oqimi: boshida xesh, oxirida yozish; yurish davomida fayl o'zgarsa yozilmaydi; qizilda --clear."""
        script = self.tmp / "gate.sh"
        script.write_text(
            f'set -e\nG="{sys.executable} {SCRIPT}"\n$G --clear\ntree=$($G --tree)\neval "$MID"\nif [ "$RED" = 1 ]; then $G --clear; exit 1; fi\n'
            '$G --write --tree "$tree"\n')
        run = lambda **extra: subprocess.run(["bash", str(script)], capture_output=True, text=True, env=dict(self.env, **dict({"MID": ":", "RED": "0"}, **extra)), cwd=str(self.repo))
        self.assertEqual(run().returncode, 0)
        self.assertTrue(self.stamp_file().exists())
        self.assertEqual(run(RED="1").returncode, 1)
        self.assertFalse(self.stamp_file().exists())
        r = run(MID="echo drift > drift.txt")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("o'zgardi", r.stderr)
        self.assertFalse(self.stamp_file().exists())


class CheckTests(GateTestCase):
    def test_check_passes_when_the_stamp_tree_equals_head_tree(self):
        self.assertEqual(self.run_gate("--write").returncode, 0)
        r = self.run_gate("--check")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(self.head_tree(), r.stdout)

    def test_check_fails_without_a_stamp(self):
        r = self.run_gate("--check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("iz yo'q", r.stderr)
        self.assertIn("sos-ci-ok", r.stderr)

    def test_check_fails_for_uncommitted_changes_even_with_a_fresh_stamp(self):
        """Iz ishchi daraxt uchun yoziladi; commit qilinmagan o'zgarish HEAD daraxtidan farq qiladi (merge hook i ham HEAD ni solishtiradi)."""
        write(self.repo, "new.txt")
        self.assertEqual(self.run_gate("--write").returncode, 0)
        r = self.run_gate("--check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("boshqa kod uchun", r.stderr)
        self.assertIn(self.head_tree(), r.stderr)
        commit_all(self.repo)
        self.assertEqual(self.run_gate("--check").returncode, 0)

    def test_check_fails_after_a_new_commit(self):
        self.assertEqual(self.run_gate("--write").returncode, 0)
        write(self.repo, "later.txt")
        commit_all(self.repo)
        self.assertEqual(self.run_gate("--check").returncode, 1)

    def test_check_with_head_option_compares_with_that_commit(self):
        first = git(self.repo, "rev-parse", "HEAD")
        write(self.repo, "second.txt")
        second = commit_all(self.repo)
        self.assertEqual(self.run_gate("--write").returncode, 0)  # iz ikkinchi commit daraxti uchun
        self.assertEqual(self.run_gate("--check", "--head", second).returncode, 0)
        self.assertEqual(self.run_gate("--check", "--head", "HEAD").returncode, 0)
        r = self.run_gate("--check", "--head", first)
        self.assertEqual(r.returncode, 1)
        self.assertIn(first, r.stderr)
        git(self.repo, "reset", "-q", "--hard", first)
        self.assertEqual(self.run_gate("--check").returncode, 1)
        self.assertEqual(self.run_gate("--check", "--head", second).returncode, 0)  # iz hali ikkinchi commit uchun

    def test_check_with_unknown_head_fails(self):
        self.assertEqual(self.run_gate("--write").returncode, 0)
        r = self.run_gate("--check", "--head", "f" * 40)
        self.assertEqual(r.returncode, 1)
        self.assertIn("aniqlanmadi", r.stderr)

    def test_check_fails_for_a_malformed_stamp(self):
        for content in ("", "\n", "bu daraxt emas 2026-01-01T00:00:00Z\n", "ABCDEF" * 7 + "ab 2026\n", " 2026-01-01T00:00:00Z\n"):
            with self.subTest(content=content):
                self.stamp_file().write_text(content)
                r = self.run_gate("--check")
                self.assertEqual(r.returncode, 1)
                self.assertIn("iz buzuq", r.stderr)

    def test_check_in_a_repo_without_commits_fails_on_head(self):
        fresh = self.tmp / "fresh"
        git(self.tmp, "init", "-q", "-b", "main", str(fresh))
        self.assertEqual(self.run_gate("--write", cwd=fresh).returncode, 0)
        r = self.run_gate("--check", cwd=fresh)
        self.assertEqual(r.returncode, 1)
        self.assertIn("HEAD daraxti aniqlanmadi", r.stderr)


class LayoutTests(GateTestCase):
    """Worktree va submodule da iz o'z git papkasida; ishchi daraxt ham o'zlariniki."""

    def test_worktree_stamp_lives_in_the_worktree_git_dir(self):
        wt = self.tmp / "wt"
        git(self.repo, "worktree", "add", "-q", "-b", "wt-branch", str(wt))
        write(wt, "only-in-worktree.txt")
        write(self.repo, "only-in-main.txt")
        wt_tree, main_tree = self.tree(cwd=wt), self.tree()
        self.assertNotEqual(wt_tree, main_tree)
        self.assertEqual(self.run_gate("--write", cwd=wt).returncode, 0)
        stamp = self.stamp_file(repo=wt)
        self.assertIn(os.path.join("worktrees", "wt"), str(stamp))
        self.assertEqual(stamp.read_text().split()[0], wt_tree)
        self.assertFalse(self.stamp_file().exists())  # asosiy checkout ning izi yo'q
        commit_all(wt)
        self.assertEqual(wt_tree, self.head_tree(wt))
        self.assertEqual(self.run_gate("--check", cwd=wt).returncode, 0)
        self.assertEqual(self.run_gate("--check").returncode, 1)
        self.assertEqual(self.run_gate("--clear", cwd=wt).returncode, 0)
        self.assertFalse(stamp.exists())

    def test_submodule_stamp_lives_in_the_modules_git_dir(self):
        src = self.tmp / "sub-src"
        git(self.tmp, "init", "-q", "-b", "main", str(src))
        write(src, "lib.txt")
        commit_all(src)
        git(self.repo, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(src), "vendor/sub")
        sub = self.repo / "vendor" / "sub"
        write(sub, "changed.txt")
        tree = self.tree(cwd=sub)
        self.assertEqual(self.run_gate("--write", "--tree", tree, cwd=sub).returncode, 0)
        stamp = self.stamp_file(repo=sub)
        self.assertIn(os.path.join(".git", "modules"), str(stamp))
        self.assertEqual(stamp.read_text().split()[0], tree)
        self.assertFalse(self.stamp_file().exists())
        commit_all(sub)
        self.assertEqual(self.run_gate("--check", cwd=sub).returncode, 0)
        self.assertEqual(self.tree(), self.tree(cwd=self.repo))  # asosiy repo xeshi submodule iziga bog'liq emas


class ConfigTests(GateTestCase):
    def test_sos_prefix_gives_sos_scrum_stamp_name(self):
        self.assertEqual(self.run_gate("--write").returncode, 0)
        self.assertTrue((self.git_dir / "sos-ci-ok").is_file())
        self.assertFalse((self.git_dir / "quality-ci-ok").exists())

    def test_default_prefix_is_quality(self):
        for env in (dict(self.env, QUALITY_CONFIG=str(self.tmp / "yoq.json")), dict(self.env, QUALITY_CONFIG=str(self.cfg))):
            with self.subTest(config=env["QUALITY_CONFIG"]):
                if env["QUALITY_CONFIG"] == str(self.cfg):
                    self.cfg.write_text("{}")
                r = self.run_gate("--write", env=env)
                self.assertEqual((r.returncode, r.stderr), (0, ""))
                self.assertTrue((self.git_dir / "quality-ci-ok").is_file())
                self.assertEqual(self.run_gate("--check", env=env).returncode, 0)
                (self.git_dir / "quality-ci-ok").unlink()

    def test_config_option_beats_environment_beats_script_directory(self):
        # skript nusxasi yonida config.json: o'rnatilgan loyiha tartibi (.claude/quality/gate_stamp.py + config.json)
        installed = self.tmp / "installed"
        installed.mkdir()
        copy = installed / "gate_stamp.py"
        shutil.copyfile(SCRIPT, copy)
        (installed / "config.json").write_text(json.dumps({"prefix": "nextto"}))
        env = {k: v for k, v in self.env.items() if k != "QUALITY_CONFIG"}
        run = lambda *a, env=env: subprocess.run([sys.executable, str(copy), *a], capture_output=True, text=True, env=env, cwd=str(self.repo))
        self.assertEqual(run("--write").returncode, 0)
        self.assertTrue((self.git_dir / "nextto-ci-ok").is_file())
        other = self.tmp / "env.json"
        other.write_text(json.dumps({"prefix": "viaenv"}))
        self.assertEqual(run("--write", env=dict(env, QUALITY_CONFIG=str(other))).returncode, 0)
        self.assertTrue((self.git_dir / "viaenv-ci-ok").is_file())
        arg = self.tmp / "arg.json"
        arg.write_text(json.dumps({"prefix": "viaarg"}))
        self.assertEqual(run("--write", "--config", str(arg), env=dict(env, QUALITY_CONFIG=str(other))).returncode, 0)
        self.assertTrue((self.git_dir / "viaarg-ci-ok").is_file())

    def test_no_config_anywhere_uses_defaults_silently(self):
        bare = self.tmp / "bare"
        bare.mkdir()
        copy = bare / "gate_stamp.py"
        shutil.copyfile(SCRIPT, copy)
        env = {k: v for k, v in self.env.items() if k != "QUALITY_CONFIG"}
        r = subprocess.run([sys.executable, str(copy), "--write"], capture_output=True, text=True, env=env, cwd=str(self.repo))
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        self.assertTrue((self.git_dir / "quality-ci-ok").is_file())

    def test_broken_config_falls_back_to_defaults_with_one_warning_line(self):
        self.cfg.write_text("{ bu JSON emas")
        r = self.run_gate("--write")
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stderr.splitlines()
        self.assertEqual(len(lines), 1, r.stderr)
        self.assertTrue(lines[0].startswith("ogohlantirish: "), lines[0])
        self.assertIn("standart sozlama ishlatiladi", lines[0])
        self.assertTrue((self.git_dir / "quality-ci-ok").is_file())

    def test_non_object_and_invalid_prefix_fall_back_with_a_warning(self):
        for body in ("[]", "5", '"sos"', json.dumps({"prefix": "a/b"}), json.dumps({"prefix": "../x"}), json.dumps({"prefix": ""}),
                     json.dumps({"prefix": 5}), json.dumps({"prefix": "-x"}), json.dumps({"prefix": "a b"})):
            with self.subTest(body=body):
                self.cfg.write_text(body)
                r = self.run_gate("--write")
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(len(r.stderr.splitlines()), 1, r.stderr)
                self.assertTrue((self.git_dir / "quality-ci-ok").is_file())
                (self.git_dir / "quality-ci-ok").unlink()
        self.assertEqual([p for p in os.listdir(self.git_dir) if p.endswith("-ci-ok")], [])

    def test_valid_prefix_shapes_are_accepted(self):
        for prefix in ("acme", "a.b", "my-proj", "x_1", "Z9"):
            with self.subTest(prefix=prefix):
                self.cfg.write_text(json.dumps({"prefix": prefix}))
                r = self.run_gate("--write")
                self.assertEqual((r.returncode, r.stderr), (0, ""))
                self.assertTrue((self.git_dir / f"{prefix}-ci-ok").is_file())

    def test_keys_of_other_scripts_in_the_shared_config_are_ignored(self):
        self.cfg.write_text(json.dumps({"prefix": "sos", "ref": "origin/main", "protected": ["main"], "reviewer": "x", "quality_log": {"file": "q.md"}}))
        r = self.run_gate("--write")
        self.assertEqual((r.returncode, r.stderr), (0, ""))


class CommandLineTests(GateTestCase):
    def test_script_is_executable_python_with_shebang_and_compiles(self):
        self.assertTrue(os.access(SCRIPT, os.X_OK))
        self.assertEqual(SCRIPT.read_text().splitlines()[0], "#!/usr/bin/env python3")
        self.assertEqual(subprocess.run([sys.executable, "-m", "py_compile", str(SCRIPT)], capture_output=True, cwd=str(self.tmp)).returncode, 0)
        r = subprocess.run([str(SCRIPT), "--tree"], capture_output=True, text=True, env=self.env, cwd=str(self.repo))  # shebang orqali
        self.assertEqual((r.returncode, r.stdout.strip()), (0, self.head_tree()))

    def test_usage_errors_exit_2_and_touch_nothing(self):
        stamp = self.stamp_file()
        stamp.write_text(f"{self.head_tree()} 2026-01-01T00:00:00Z\n")
        for args in ((), ("--tree", "--write"), ("--write", "--clear"), ("--check", "--clear"), ("--head", "abc"), ("--write", "--head", "abc"),
                     ("--tree", self.head_tree()), ("--clear", "--tree", self.head_tree()), ("--check", "--head", "--x"), ("--check", "--head", "-abc"),
                     ("--check", "--head", ""), ("--bilinmaydi",), ("--wr",)):
            with self.subTest(args=args):
                r = self.run_gate(*args)
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertEqual(r.stdout, "")
                self.assertTrue(stamp.exists())

    def test_outside_a_repo_every_action_fails_with_exit_1(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        for args in (("--write",), ("--clear",), ("--check",)):
            with self.subTest(args=args):
                r = self.run_gate(*args, cwd=outside)
                self.assertEqual(r.returncode, 1)
                self.assertIn("git repo emas", r.stderr)

    def test_no_project_specific_names_in_the_script_source(self):
        text = SCRIPT.read_text().lower()
        for needle in ("sos-", "sos_", "manba", "arxitektura", "holat.md", "sifat-jurnali", "laravel", "angular", "make ci"):
            self.assertNotIn(needle, text, needle)


if __name__ == "__main__":
    unittest.main()
