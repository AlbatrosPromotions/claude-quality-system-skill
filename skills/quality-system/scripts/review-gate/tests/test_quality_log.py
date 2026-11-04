#!/usr/bin/env python3
"""quality_log.py testlari: topilmalar jurnali formati va "topilma → qoida" majburiyati.

Qora quti: skript subprocess orqali ishga tushiriladi, hamma git repo vaqtinchalik papkada, sozlama QUALITY_CONFIG orqali
(haqiqiy loyiha sozlamasi va git konfigiga tegilmaydi). Ikki joylashuvni biladi: to'plam papkasi va o'rnatilgan loyiha (.claude/quality).
Ishga tushirishlar sarflanadigan vaqt uchun ko'p qiymat bitta jurnalga (har biri o'z qatorida, o'z kategoriyasida) yig'iladi.

  python3 -m unittest discover -s tests -p 'test_quality_log.py' -v                 # to'plam papkasidan
  python3 -m unittest discover -s .claude/quality/tests -p 'test_quality_log.py' -v  # o'rnatilgan loyihadan
"""
import datetime
import itertools
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

QUALITY = Path(__file__).resolve().parent.parent  # to'plam papkasi yoki <loyiha>/.claude/quality: skript ikkala joyda ham tests/ ning ota papkasida
SCRIPT = QUALITY / "quality_log.py"

HOME = tempfile.mkdtemp(prefix="quality-log-home-")
ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_") and k not in ("QUALITY_CONFIG", "CLAUDE_PROJECT_DIR")}
ENV.update(
    HOME=HOME, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1",
    GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com",
)

TODAY = datetime.date(2026, 10, 24)
TODAY_ISO = TODAY.isoformat()
FRESH = TODAY_ISO
FINDINGS_HEADER = "| Date | PR | Category | Severity | Caught by | Rule |"
CANARY_HEADER = "| Date | Branch | Planted | Caught | Missed (category) | Rate | Note |"
# birinchi topilma qatori shu satrda (satrlar 1 dan sanaladi)
FIRST_FINDING_LINE = 9
NAMED_ERROR = "nomlangan tekshiruv mavjud tekshiruv"  # "...faylini" yoki "...fayli yoki darvoza bosqichini ko'rsatmaydi"

GATE_SCRIPT = """\
# stage <name> <dir> <command...>: comment
# stage commented-out . true
stage() {
  local name="$1"
}
stage canary . bash ci/canary.sh
  stage contracts . python3 tools/contracts.py --check
stage sync-i18n web pnpm run sync:i18n || exit 1
"""
GATE_CONFIG = {"quality_log": {"gate_script": "ci/gate.sh"}}
FIXTURE_FILES = {
    "e2e/a11y/geometry.ts": "", "tools/x.py": "", "tools/run.sh": "", "tools/a|b.py": "", "tests/x_test.py": "", "scripts/check.sh": "", "docs/x.md": "", "qa/run.sh": "",
    "ops/run.sh": "", "lab/run.sh": "", "src/util_test.py": "", "docs/runbook/README.md": "", "ci/gate.sh": GATE_SCRIPT,
    ".claude/agents/spec-writer.md": "", "docs/status.md": "", ".gitignore": "tools/generated/\n.env\n",
}
# git kuzatmaydigan fayllar: ignor qilingan va qo'shilmagan (ikkalasi ham tekshiruv papkasi ostida)
UNTRACKED_FILES = ("tools/generated/out.py", ".env", "tools/fresh.py")
# submodule (gitlink, rejim 160000): `git ls-files` unga papka nomini beradi; ichidagi fayl ota repoda kuzatilmaydi.
# Tekshiruv prefiksi (tools/) ostida turadi: gitlink istisnosi bo'lmasa nomi tekshiruv deb qabul qilinardi
SUBMODULE = "tools/vendored"
SUBMODULE_FILE = f"{SUBMODULE}/lib.py"

SCRATCH = None
FIXTURE_ROOT = None
NO_CONFIG = None


def git(cwd, *args, check=True):
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, env=ENV)
    if check and r.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} ({cwd}): {r.stderr}")
    return r.stdout.strip()


def make_repo(root, tracked, untracked=(), gitlinks=()):
    """root da git repo: `tracked` indeksga qo'shiladi (commit shart emas), `untracked` faqat diskda, `gitlinks` indeksda submodule (gitlink) bo'lib turadi."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q")
    contents = dict(tracked) if isinstance(tracked, dict) else {name: "" for name in tracked}
    for name, content in [*contents.items(), *((name, "") for name in untracked)]:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(content)
    if contents:
        git(root, "add", "--", *contents)
    for name in gitlinks:
        git(root, "update-index", "--add", "--cacheinfo", f"160000,{'a' * 40},{name}")
    return root


def setUpModule():
    global SCRATCH, FIXTURE_ROOT, NO_CONFIG
    SCRATCH = Path(tempfile.mkdtemp(prefix="quality-log-scratch-"))
    NO_CONFIG = SCRATCH / "empty-config.json"
    NO_CONFIG.write_text("{}")
    FIXTURE_ROOT = SCRATCH / "project"
    (FIXTURE_ROOT / SUBMODULE).mkdir(parents=True)
    (FIXTURE_ROOT / SUBMODULE_FILE).write_text("")
    make_repo(FIXTURE_ROOT, FIXTURE_FILES, untracked=UNTRACKED_FILES, gitlinks=(SUBMODULE,))


def tearDownModule():
    shutil.rmtree(HOME, ignore_errors=True)
    shutil.rmtree(SCRATCH, ignore_errors=True)


_row_seq = itertools.count(1)


def row(date="2026-10-20", pr="#1", category="ui.corner-bleed", severity="major", caught=None, rule="`e2e/a11y/geometry.ts`"):
    # standart "Caught by" har qatorda har xil: bir xil birinchi besh katak takroriy qator xatosi (DuplicateTest)
    if caught is None:
        caught = f"lead {next(_row_seq)}"
    return f"| {date} | {pr} | {category} | {severity} | {caught} | {rule} |"


def canary_row(date="2026-10-24", branch="canary-1", planted="4", caught="3", missed="auth.x", rate="3/4", note="-"):
    return f"| {date} | {branch} | {planted} | {caught} | {missed} | {rate} | {note} |"


def journal(findings=(), canary=(), findings_header=FINDINGS_HEADER, canary_header=CANARY_HEADER, separator="|---|---|---|---|---|---|", with_canary=True):
    lines = ["# Quality log", "", "## Rules", "1. rule", "", "## Findings", findings_header, separator, *findings, ""]
    if with_canary:
        lines += ["## Critic canary", "Description.", "", canary_header, "|---|---|---|---|---|---|---|", *canary, ""]
    return "\n".join(lines)


def run_cli(*args, config=None, env_extra=None, cwd=None, script=None):
    """Skriptni ishga tushiradi. config: dict/satr (QUALITY_CONFIG faylga yoziladi), yo'l (Path) yoki None = bo'sh config; env_extra qiymati None = o'zgaruvchini olib tashlash."""
    env = dict(ENV)
    if isinstance(config, (dict, str)):
        path = SCRATCH / "config.json"
        path.write_text(config if isinstance(config, str) else json.dumps(config), encoding="utf-8")
        config = path
    env["QUALITY_CONFIG"] = str(config or NO_CONFIG)
    for key, value in (env_extra or {}).items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return subprocess.run([sys.executable, str(script or SCRIPT), *args], capture_output=True, text=True, env=env, cwd=cwd)


def parse_errors(stderr):
    """'  <fayl>:<satr>: xabar' qatorlaridan [(satr raqami, xabar)]; satr raqamsiz xabar 0 satr."""
    errors = []
    for line in stderr.splitlines():
        m = re.match(r"^  (.*?quality-log\.md)(?::(\d+))?: (.*)$", line)
        if m:
            errors.append((int(m.group(2) or 0), m.group(3)))
    return errors


def check(text, today=TODAY, root=None, config=None, extra=()):
    """Jurnal matnini tekshiradi; hisobot qatorlari [(satr raqami, xabar)]. Chiqish kodi xatolar bilan mos kelishi shart (0 = yashil, 1 = qizil)."""
    path = SCRATCH / "quality-log.md"
    path.write_text(text, encoding="utf-8")
    result = run_cli("--check", "--file", str(path), "--root", str(root or FIXTURE_ROOT), "--today", str(today), *extra, config=config)
    errors = parse_errors(result.stderr)
    if errors:
        if result.returncode != 1 or "QIZIL" not in result.stderr:
            raise AssertionError(f"xatolar bor, lekin chiqish kodi {result.returncode}: {result.stderr}")
    elif result.returncode != 0 or not result.stdout.startswith("quality_log:"):
        raise AssertionError(f"xato yo'q, lekin chiqish kodi {result.returncode}: {result.stdout}{result.stderr}")
    return errors


def rule_errors(rules, **kwargs):
    """Har qoida o'z qatorida (o'z kategoriyasida), hammasi bitta ishga tushirishda: har qoida uchun [xabarlar]."""
    rows = [row(category=f"r{i}.x", caught=f"c{i}", rule=rule) for i, rule in enumerate(rules)]
    errors = check(journal(rows), **kwargs)
    return [[msg for n, msg in errors if n == FIRST_FINDING_LINE + i] for i in range(len(rules))]


def field_errors(field, values, **kwargs):
    """Maydonning har noto'g'ri qiymati o'z qatorida (qolgan kataklar to'g'ri), bitta ishga tushirishda: har qiymat uchun [xabarlar]."""
    rows = []
    for i, value in enumerate(values):
        cells = {"category": f"v{i}.x", "caught": f"c{i}", "rule": "-"}
        cells[field] = value
        rows.append(row(**cells))
    errors = check(journal(rows), **kwargs)
    return [[msg for n, msg in errors if n == FIRST_FINDING_LINE + i] for i in range(len(values))]


class RuleCase(unittest.TestCase):
    """Qoida katagi sinovlari uchun yordamchilar: har qoida alohida subTest, hammasi bitta ishga tushirishda."""

    def assertNamed(self, rules, **kwargs):
        for rule, messages in zip(rules, rule_errors(rules, **kwargs)):
            with self.subTest(rule=rule):
                self.assertTrue(any(NAMED_ERROR in m for m in messages), messages)

    def assertAccepted(self, rules, **kwargs):
        for rule, messages in zip(rules, rule_errors(rules, **kwargs)):
            with self.subTest(rule=rule):
                self.assertEqual(messages, [])

    def assertRuleError(self, rules, needle, **kwargs):
        for rule, messages in zip(rules, rule_errors(rules, **kwargs)):
            with self.subTest(rule=rule):
                self.assertTrue(any(needle in m for m in messages), messages)


class FindingsTest(unittest.TestCase):
    def assertOk(self, text, today=TODAY):
        self.assertEqual(check(text, today), [])

    def assertError(self, text, line, needle, today=TODAY):
        errors = check(text, today)
        self.assertTrue(any(n == line and needle in msg for n, msg in errors), f"{line}-satrda {needle!r} kutilgan: {errors}")

    def assertBad(self, field, values, needle):
        for value, messages in zip(values, field_errors(field, values)):
            with self.subTest(**{field: value}):
                self.assertTrue(any(needle in msg for msg in messages), messages)

    def test_empty_tables_are_ok(self):
        self.assertOk(journal())

    def test_valid_rows(self):
        self.assertOk(journal([row(), row(category="a.b.c", severity="minor", rule="-"), row(pr="-", category="x1.y-2", rule="planned: session 38")]))

    def test_date_format(self):
        self.assertBad("date", ("24.10.2026", "2026-1-5", "2026-13-45", "2026-02-30", "", "2026-10-24T10:00"), "Date")

    def test_pr_format(self):
        self.assertBad("pr", ("40", "#", "#x", "# 4", "PR-4", ""), "PR")

    def test_category_format(self):
        self.assertBad("category", ("ui", "Ui.corner", "ui.Corner", "ui_corner.x", "ui..x", ".ui", "ui.", "ui.corner bleed", ""), "Category")

    def test_severity(self):
        # eski o'zbekcha darajalar ham endi noto'g'ri: generik format faqat blocker | major | minor
        self.assertBad("severity", ("Blocker", "critical", "", "bloker", "muhim", "mayda"), "Severity")

    def test_all_three_severities_are_valid(self):
        self.assertOk(journal([row(category=f"s{i}.x", severity=level) for i, level in enumerate(("blocker", "major", "minor"))]))

    def test_how_caught_required(self):
        self.assertError(journal([row(caught="")]), FIRST_FINDING_LINE, "Caught by")

    def test_rule_required(self):
        self.assertError(journal([row(rule="")]), FIRST_FINDING_LINE, "Rule bo'sh")

    def test_planned_needs_text_and_exact_spelling(self):
        rules = ["planned:", "Planned: session 38", "planned session 38"]
        for rule, messages in zip(rules, rule_errors(rules)):
            with self.subTest(rule=rule):
                self.assertTrue(any("planned:" in msg for msg in messages), messages)

    def test_wrong_cell_count(self):
        self.assertError(journal(["| 2026-10-20 | #1 | ui.x | major | lead |"]), FIRST_FINDING_LINE, "5 katak")
        self.assertError(journal([row() + " extra |"]), FIRST_FINDING_LINE, "7 katak")

    def test_row_must_end_with_pipe(self):
        self.assertError(journal([row()[:-1]]), FIRST_FINDING_LINE, "`|` bilan tugamagan")

    def test_escaped_pipe_in_cell(self):
        self.assertOk(journal([row(rule="`tools/x.py \\| --check`")]))
        # `\|` katakda belgi bo'lib qoladi: nomida "|" bor kuzatiladigan fayl (tools/a|b.py) topiladi, tools/a|c.py yo'q
        found, missing = rule_errors(["`tools/a\\|b.py`", "`tools/a\\|c.py`"])
        self.assertEqual(found, [])
        self.assertTrue(any(NAMED_ERROR in m for m in missing), missing)

    def test_header_and_separator(self):
        self.assertError(journal(findings_header="| Date | PR | Category |"), 7, "sarlavhasi")
        self.assertError(journal(separator="|a|b|c|d|e|f|"), 8, "ajratgich")

    def test_old_uzbek_headers_are_rejected(self):
        self.assertError(journal(findings_header="| Sana | PR | Kategoriya | Daraja | Qanday ushlandi | Qoida |"), 7, "sarlavhasi")

    def test_missing_findings_section(self):
        errors = check("# Quality log\n\n## Rules\n")
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0][0], 0)
        self.assertIn("## Findings", errors[0][1])
        self.assertIn("topilmadi", errors[0][1])

    def test_findings_heading_without_table_is_error(self):
        errors = check("# Quality log\n\n## Findings\nhech narsa yo'q\n")
        self.assertEqual([n for n, _ in errors], [3])
        self.assertIn("jadval yo'q", errors[0][1])

    def test_line_numbers_follow_rows(self):
        text = journal([row(), row(date="bad", category="ui.other"), row(category="ui.third", severity="x")])
        lines = sorted(n for n, _ in check(text))
        self.assertEqual(lines, [FIRST_FINDING_LINE + 1, FIRST_FINDING_LINE + 2])


class RuleObligationTest(unittest.TestCase):
    def errors(self, findings, today=TODAY):
        return check(journal(findings), today)

    def test_single_non_blocker_row_needs_no_rule(self):
        combos = [(level, rule) for level in ("major", "minor") for rule in ("-", "planned: later")]
        self.assertEqual(self.errors([row(category=f"o{i}.x", severity=level, rule=rule) for i, (level, rule) in enumerate(combos)]), [])

    def test_two_rows_without_named_check_is_red(self):
        errors = self.errors([row(rule="-"), row(date="2026-10-22", rule="-")])
        self.assertEqual(len(errors), 1)
        line, message = errors[0]
        self.assertEqual(line, FIRST_FINDING_LINE)
        self.assertIn("ui.corner-bleed", message)
        self.assertIn("2 qator", message)
        self.assertIn(f"{FIRST_FINDING_LINE}, {FIRST_FINDING_LINE + 1}", message)

    def test_two_rows_with_one_named_check_is_ok(self):
        self.assertEqual(self.errors([row(rule="-"), row(rule="`tools/x.py`")]), [])

    def test_blocker_without_named_check_is_red(self):
        errors = self.errors([row(severity="blocker", rule="-")])
        self.assertEqual(len(errors), 1)
        self.assertIn("blocker", errors[0][1])

    def test_blocker_with_named_check_is_ok(self):
        self.assertEqual(self.errors([row(severity="blocker", rule="`tests/x_test.py` regression test")]), [])

    def test_different_categories_are_independent(self):
        self.assertEqual(self.errors([row(category="a.one", rule="-"), row(category="a.two", rule="-")]), [])
        errors = self.errors([row(category="a.one", rule="-"), row(category="a.two", rule="-"), row(category="a.one", rule="-", date=FRESH)])
        self.assertEqual(len(errors), 1)
        self.assertIn("a.one", errors[0][1])

    def test_fresh_planned_counts_within_seven_days(self):
        dates = [(TODAY - datetime.timedelta(days=age)).isoformat() for age in (0, 1, 7)]
        self.assertEqual(self.errors([row(date=d, category=f"p{i}.x", severity="blocker", rule="planned: session 38") for i, d in enumerate(dates)]), [])

    def test_planned_expires_after_seven_days(self):
        sana = (TODAY - datetime.timedelta(days=8)).isoformat()
        errors = self.errors([row(date=sana, severity="blocker", rule="planned: session 38")])
        # qatorning o'zi (muddati o'tgan va'da) va kategoriya (blocker uchun nomlangan tekshiruv yo'q)
        self.assertEqual(len(errors), 2)
        self.assertTrue(all("muddati o'tgan" in message and "8 kun" in message for _, message in errors))

    def test_expiry_depends_on_injected_today(self):
        findings = [row(date="2026-10-20", severity="blocker", rule="planned: x")]
        self.assertEqual(self.errors(findings, datetime.date(2026, 10, 27)), [])
        self.assertEqual(len(self.errors(findings, datetime.date(2026, 10, 28))), 2)

    def test_any_expired_planned_row_is_red_even_with_named_check(self):
        # kategoriya majburiyati bajarilgan (nomlangan tekshiruv bor), lekin bajarilmagan va'da qatorning o'zida qizil
        errors = self.errors([row(date="2026-01-01", severity="blocker", rule="planned: x"), row(rule="`tools/x.py`")])
        self.assertEqual([n for n, _ in errors], [FIRST_FINDING_LINE])
        self.assertIn("muddati o'tgan", errors[0][1])

    def test_expired_single_non_blocker_planned_is_red(self):
        errors = self.errors([row(date="2026-01-01", category=f"e{i}.x", severity=level, rule="planned: x") for i, level in enumerate(("major", "minor"))])
        self.assertEqual([n for n, _ in errors], [FIRST_FINDING_LINE, FIRST_FINDING_LINE + 1])
        self.assertTrue(all("muddati o'tgan" in msg for _, msg in errors))

    def test_expired_planned_is_red_even_when_category_is_invalid(self):
        errors = self.errors([row(date="2026-01-01", category="Bad", rule="planned: x")])
        self.assertTrue(any("muddati o'tgan" in message for _, message in errors), errors)

    def test_expired_planned_only_flags_its_own_row(self):
        errors = self.errors([row(category="a.one", date="2026-01-01", rule="planned: x"), row(category="a.two", rule="planned: y")])
        self.assertEqual([n for n, _ in errors], [FIRST_FINDING_LINE])

    def test_future_dated_planned_counts(self):
        self.assertEqual(self.errors([row(date="2026-11-30", severity="blocker", rule="planned: x")]), [])


class RuleKindTest(RuleCase):
    def test_existing_path_is_named_check(self):
        self.assertAccepted(["`tools/x.py`", "`./tools/x.py`", "`docs/runbook/README.md` and `tools/x.py` note", "a11y `geometry` (`e2e/a11y/geometry.ts`)",
                             "`tools/x.py --check --today 2026-10-24`"])

    def test_missing_path_is_error(self):
        self.assertNamed(["`tools/missing.py`", "`e2e/missing/`", "a11y `horizontalOverflow` (320 px)", "`tools/x.py/inner`"])

    def test_directory_is_not_a_target(self):
        # keng papka nomi nasrni qoida qilib qo'yardi (`tools` yoki `e2e` har qanday matnni yopardi)
        self.assertNamed(["`e2e/a11y/`", "`e2e/a11y`", "`tools`", "`e2e`", "`.git`", "`.git/config`", "`docs/runbook/`"])

    def test_prompt_and_doc_files_are_not_checks(self):
        # agent prompti tekshiruv ko'rsatmasi, deterministik tekshiruv emas; .md hech qachon
        names = (".claude/agents/spec-writer.md", "docs/runbook/README.md", "docs/status.md")
        for name in names:
            self.assertTrue((FIXTURE_ROOT / name).is_file())
        self.assertNamed([f"`{name}` \"Check\" (not deterministic)" for name in names])
        self.assertAccepted(["`.claude/agents/spec-writer.md` and `tools/x.py`"])

    def test_untracked_or_ignored_file_is_not_a_target(self):
        for name in UNTRACKED_FILES:
            self.assertTrue((FIXTURE_ROOT / name).is_file())
        self.assertNamed([f"`{name}`" for name in UNTRACKED_FILES])

    def test_submodule_gitlink_is_not_a_target(self):
        # `git ls-files` gitlink ni ham beradi: `tools/vendored` papka bo'lsa-da kuzatiladigan "fayl" bo'lib ko'rinardi
        index = git(FIXTURE_ROOT, "ls-files", "-s", SUBMODULE)
        self.assertTrue(index.startswith("160000 "), f"fixture: gitlink indeksda bo'lishi shart: {index!r}")
        self.assertNamed([f"`{SUBMODULE}`", f"`{SUBMODULE}/`", f"`{SUBMODULE_FILE}`"])
        self.assertAccepted(["`tools/x.py`"])

    def test_root_outside_git_has_no_targets(self):
        plain = Path(tempfile.mkdtemp(prefix="quality-log-plain-"))
        self.addCleanup(shutil.rmtree, plain, True)
        (plain / "tools").mkdir()
        (plain / "tools" / "x.py").write_text("")
        path = SCRATCH / "quality-log.md"
        path.write_text(journal([row(rule="`tools/x.py`")]), encoding="utf-8")
        # git ota papkalardan repo qidirmasin: fayl diskda bor, lekin hech qaysi indeksda emas
        result = run_cli("--check", "--file", str(path), "--root", str(plain), "--today", TODAY_ISO,
                         env_extra={"GIT_CEILING_DIRECTORIES": os.path.realpath(plain.parent)})
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(NAMED_ERROR, result.stderr)

    def test_path_outside_root_is_not_a_target(self):
        self.assertNamed(["`..`", "`../tools/x.py`", "`tools/../../x`", "`/etc`", "`/`", "`.`", "`./`"])

    def test_path_without_backticks_is_not_enough(self):
        self.assertNamed(["tools/x.py"])

    def test_gate_stage_name_is_named_check(self):
        self.assertAccepted(["`canary`", "stage `contracts` of the gate", "`sync-i18n`"], config=GATE_CONFIG)

    def test_unknown_or_commented_stage_is_error(self):
        self.assertNamed(["`unknown-stage`", "`commented-out`", "`stage`", "`name`", "canary stage", "`make ci`"], config=GATE_CONFIG)

    def test_stage_name_must_be_whole_token(self):
        self.assertNamed(["`canar`", "`canary-x`", "`contracts-x`"], config=GATE_CONFIG)

    def test_stage_names_need_gate_script_in_config(self):
        # gate_script berilmasa skript fayli bor bo'lsa ham bosqich nomlari hisobga olinmaydi
        stages = ["`canary`", "`contracts`", "`sync-i18n`"]
        self.assertNamed(stages)
        self.assertAccepted(stages, config=GATE_CONFIG)

    def test_missing_gate_script_gives_no_stages(self):
        self.assertNamed(["`canary`"], config={"quality_log": {"gate_script": "ci/does-not-exist.sh"}})
        with tempfile.TemporaryDirectory() as empty:
            self.assertNamed(["`canary`"], root=empty, config=GATE_CONFIG)

    def test_prose_only_is_error(self):
        self.assertNamed(["make ci canary", "unit test somewhere", "will be built next session", "there is a check"])

    def test_prose_rule_does_not_satisfy_blocker(self):
        errors = check(journal([row(severity="blocker", rule="unit test somewhere")]))
        self.assertEqual(len(errors), 2)
        self.assertTrue(any(NAMED_ERROR in msg for _, msg in errors))
        self.assertTrue(any("Category" in msg and "blocker" in msg for _, msg in errors))

    def test_error_message_lists_what_is_accepted(self):
        message = rule_errors(["prose"])[0][0]
        self.assertIn("tests/", message)
        self.assertIn("scripts/", message)
        self.assertIn("accepted: <matn>", message)
        self.assertNotIn("darvoza skripti", message)  # gate_script yo'q: bosqich nomi haqida gap yo'q
        self.assertIn("darvoza skripti `ci/gate.sh`", rule_errors(["prose"], config=GATE_CONFIG)[0][0])

    def test_accepted_is_accepted(self):
        self.assertAccepted(["accepted: the script cannot detect it", "accepted:once"])

    def test_accepted_satisfies_category_requirement(self):
        findings = [row(rule="-"), row(rule="accepted: the meaning of the text ages")]
        self.assertEqual(check(journal(findings)), [])

    def test_accepted_does_not_close_a_blocker_category(self):
        # blocker kategoriya deterministik qoidaga aylanadi: `accepted:` uni yopmaydi
        for findings in ([row(severity="blocker", rule="accepted: once")],
                         [row(severity="blocker", rule="-"), row(rule="accepted: once")]):
            with self.subTest(findings=findings):
                errors = check(journal(findings))
                self.assertTrue(any("Category" in msg and "blocker" in msg for _, msg in errors), errors)
        self.assertEqual(check(journal([row(severity="blocker", rule="accepted: x"), row(rule="`tools/x.py`")])), [])
        self.assertEqual(check(journal([row(severity="blocker", rule=f"planned: {FRESH}")])), [])

    def test_empty_accepted_is_error(self):
        self.assertRuleError(["accepted:"], "`accepted:` dan keyin")

    def test_empty_accepted_does_not_satisfy_category(self):
        errors = check(journal([row(rule="-"), row(rule="accepted:")]))
        self.assertTrue(any("Category" in msg for _, msg in errors), errors)

    def test_accepted_misspelling_is_error(self):
        self.assertRuleError(["Accepted: text", "ACCEPTED: text", "accepted text", "accepted - text", "accepted; text", "accepted`tools/x.py`"],
                             "`accepted:` (kichik harf, ikki nuqta)")

    def test_misspelt_accepted_does_not_satisfy_category(self):
        errors = check(journal([row(rule="-"), row(rule="Accepted: text")]))
        self.assertTrue(any("Category" in msg for _, msg in errors), errors)

    def test_accepted_not_at_start_is_ordinary_prose(self):
        self.assertNamed(["note accepted: text"])


class RecurrenceTest(unittest.TestCase):
    L = FIRST_FINDING_LINE

    def errors(self, findings, today=TODAY):
        return check(journal(findings), today)

    def test_two_rows_after_last_rule_is_red(self):
        errors = self.errors([row(), row(rule="-"), row(rule="-")])
        self.assertEqual([n for n, _ in errors], [self.L + 2])
        message = errors[0][1]
        self.assertIn("ui.corner-bleed", message)
        self.assertIn(f"{self.L}-satrdagi", message)
        self.assertIn(f"{self.L + 1}, {self.L + 2}", message)
        self.assertIn("accepted:", message)

    def test_one_row_after_last_rule_is_ok(self):
        self.assertEqual(self.errors([row(), row(rule="-")]), [])

    def test_accepted_is_the_last_rule(self):
        findings = [row(), row(rule="accepted: text"), row(rule="-"), row(rule="-")]
        errors = self.errors(findings)
        self.assertEqual([n for n, _ in errors], [self.L + 3])
        self.assertIn(f"{self.L + 1}-satrdagi", errors[0][1])

    def test_latest_named_row_resets_count(self):
        self.assertEqual(self.errors([row(), row(rule="-"), row(rule="-"), row(rule="`tools/x.py`")]), [])

    def test_fresh_accepted_on_latest_row_resets(self):
        self.assertEqual(self.errors([row(), row(rule="-"), row(rule="-"), row(rule="accepted: new reason")]), [])

    def test_fresh_planned_on_latest_row_resets(self):
        self.assertEqual(self.errors([row(), row(rule="-"), row(rule="-"), row(date=FRESH, rule="planned: session 41")]), [])

    def test_expired_planned_does_not_reset(self):
        findings = [row(), row(rule="-"), row(date="2026-10-01", rule="planned: old")]
        errors = self.errors(findings)
        self.assertEqual(sorted(n for n, _ in errors), [self.L + 2, self.L + 2])
        self.assertTrue(any("muddati o'tgan" in msg for _, msg in errors))
        self.assertTrue(any("qoida yoki yopilishdan keyin" in msg for _, msg in errors))

    def test_live_planned_is_the_last_rule(self):
        findings = [row(rule="-"), row(date=FRESH, rule="planned: x"), row(rule="-"), row(rule="-")]
        errors = self.errors(findings)
        self.assertEqual([n for n, _ in errors], [self.L + 3])
        self.assertIn(f"{self.L + 1}-satrdagi", errors[0][1])

    def test_rule_row_is_found_in_file_order_not_by_date(self):
        findings = [row(date="2026-10-24", rule="-"), row(date="2026-10-01"), row(date="2026-10-23", rule="-")]
        self.assertEqual(self.errors(findings), [])

    def test_other_categories_between_rows_do_not_count(self):
        findings = [row(), row(category="a.one", rule="-"), row(category="a.two", rule="-"), row(rule="-")]
        self.assertEqual(self.errors(findings), [])

    def test_categories_are_independent(self):
        findings = [row(), row(category="a.one"), row(rule="-"), row(category="a.one", rule="-"), row(category="a.one", rule="-")]
        errors = self.errors(findings)
        self.assertEqual([n for n, _ in errors], [self.L + 4])
        self.assertIn("a.one", errors[0][1])

    def test_blocker_row_after_rule_counts_like_any_row(self):
        self.assertEqual(len(self.errors([row(), row(severity="blocker", rule="-"), row(severity="blocker", rule="-")])), 1)

    def test_category_without_any_rule_reports_missing_rule_not_recurrence(self):
        errors = self.errors([row(rule="-"), row(rule="-"), row(rule="-")])
        self.assertEqual(len(errors), 1)
        self.assertIn("nomlangan tekshiruv, `accepted:` (blocker bo'lmasa) yoki amaldagi `planned:` yo'q", errors[0][1])


class DuplicateTest(unittest.TestCase):
    SAME = {"date": "2026-10-20", "pr": "#80", "category": "docs.stale-instruction", "severity": "major", "caught": "reviewer"}

    def errors(self, rows):
        return check(journal(rows))

    def duplicates(self, rows):
        return [(n, msg) for n, msg in self.errors(rows) if "takroriy" in msg]

    def test_same_first_five_cells_is_red_naming_both_lines(self):
        # haqiqiy voqea: "keep both sides" merge eski `planned:` nusxani yopilgan nusxa yonida qoldirdi, faqat Rule farq qilardi
        found = self.duplicates([row(**self.SAME, rule="planned: changelog check"), row(**self.SAME, rule="`tools/x.py`")])
        self.assertEqual([n for n, _ in found], [FIRST_FINDING_LINE + 1])
        self.assertIn(f"{FIRST_FINDING_LINE}-satr bilan takroriy", found[0][1])
        self.assertIn("merge artefakti", found[0][1])

    def test_identical_rows_are_red_too(self):
        self.assertEqual(len(self.duplicates([row(**self.SAME), row(**self.SAME)])), 1)

    def test_non_adjacent_duplicates_are_found(self):
        rows = [row(**self.SAME), row(category="auth.x", rule="-"), row(**self.SAME)]
        self.assertEqual([n for n, _ in self.duplicates(rows)], [FIRST_FINDING_LINE + 2])

    def test_three_copies_report_each_later_copy_against_the_first(self):
        found = self.duplicates([row(**self.SAME), row(**self.SAME), row(**self.SAME)])
        self.assertEqual([n for n, _ in found], [FIRST_FINDING_LINE + 1, FIRST_FINDING_LINE + 2])
        self.assertTrue(all(f"{FIRST_FINDING_LINE}-satr bilan" in msg for _, msg in found))

    def test_same_description_but_different_date_is_ok(self):
        self.assertEqual(self.errors([row(**self.SAME), row(**{**self.SAME, "date": "2026-10-21"})]), [])

    def test_same_description_but_different_pr_is_ok(self):
        self.assertEqual(self.errors([row(**self.SAME), row(**{**self.SAME, "pr": "#81"})]), [])

    def test_different_category_severity_or_how_caught_is_ok(self):
        rows = [row(**self.SAME)] + [row(**{**self.SAME, field: value}) for field, value in (("category", "docs.other"), ("severity", "minor"), ("caught", "reviewer-2"))]
        self.assertEqual(self.errors(rows), [])

    def test_rule_cell_alone_does_not_make_rows_distinct(self):
        self.assertEqual(len(self.duplicates([row(**self.SAME, rule="-"), row(**self.SAME, rule="accepted: reason")])), 1)

    def test_canary_table_is_not_checked(self):
        self.assertEqual(check(journal(canary=[canary_row(), canary_row()])), [])

    def test_command_line_reports_duplicate_with_file_lines(self):
        path = SCRATCH / "quality-log.md"
        path.write_text(journal([row(**self.SAME, rule="planned: x"), row(**self.SAME, rule="`tools/x.py`")]), encoding="utf-8")
        result = run_cli("--check", "--file", str(path), "--root", str(FIXTURE_ROOT), "--today", FRESH)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(f"quality-log.md:{FIRST_FINDING_LINE + 1}:", result.stderr)
        self.assertIn("takroriy", result.stderr)


class TableBreakTest(unittest.TestCase):
    def breaks(self, text):
        return [(n, msg) for n, msg in check(text) if "jadval uzilgan" in msg]

    def test_blank_line_inside_findings_table_is_red_naming_the_line(self):
        # uzilishdan keyingi qator buzuq bo'lsa ham (noto'g'ri sana) avval jim o'tib ketardi
        text = journal([row(), "", row(date="bad", category="ui.other")])
        found = self.breaks(text)
        self.assertEqual([n for n, _ in found], [FIRST_FINDING_LINE + 2])
        self.assertIn(f"{FIRST_FINDING_LINE}-satrdan keyingi qatorlar tekshirilmaydi", found[0][1])
        self.assertIn(f"{FIRST_FINDING_LINE + 1}-satr", found[0][1])

    def test_blank_line_right_after_separator_is_red(self):
        self.assertEqual([n for n, _ in self.breaks(journal(["", row()]))], [FIRST_FINDING_LINE + 1])

    def test_text_line_inside_findings_table_is_red(self):
        self.assertEqual([n for n, _ in self.breaks(journal([row(), "note", row(category="ui.other")]))], [FIRST_FINDING_LINE + 2])

    def test_blank_line_inside_canary_table_is_red_naming_the_line(self):
        found = self.breaks(journal(canary=[canary_row(), "", canary_row()]))
        self.assertEqual([n for n, _ in found], [17])
        self.assertIn("15-satrdan keyingi", found[0][1])

    def test_only_first_stray_line_of_a_table_is_reported(self):
        self.assertEqual(len(self.breaks(journal([row(), "", row(category="ui.b"), row(category="ui.c")]))), 1)

    def test_prose_after_table_without_pipes_is_ok(self):
        self.assertEqual(check(journal([row()]) + "\nNote text.\n"), [])

    def test_normal_tables_are_ok(self):
        self.assertEqual(self.breaks(journal([row(), row(category="ui.b")], canary=[canary_row(), canary_row(date="2026-10-25")])), [])


class CheckPathTest(unittest.TestCase):
    """Standart tekshiruv yo'llari; har yo'l alohida qator (alohida kategoriya), hammasi bitta ishga tushirishda."""
    ALLOWED = (
        "tests/x_test.py", "scripts/check.sh", "tests/Feature/UserTest.php", "tests/helpers/support.rb", "test/unit/a.js", "spec/models/user_spec.rb",
        "specs/a.txt", "e2e/login.ts", "tools/x.py", "tools/lint.py", "ci/gate.sh", ".claude/hooks/guard.py", ".claude/quality/review_stamp.py",
        ".githooks/pre-push", ".github/workflows/ci.yml", "src/test_utils.py", "pkg/util_test.py", "pkg/util_test.go", "app/UserTest.php",
        "src/UserTest.java", "web/a.spec.ts", "web/a.test.ts", "web/a.spec.js", "web/a.test.js", "web/a.spec.tsx", "web/a.test.tsx", "lib/user_spec.rb",
        "Makefile", "./tools/x.py", "tools//x.py", "scripts/../tools/x.py",
    )
    DENIED = (
        "docs/x.md", "README.md", "tests/README.md", "tools/notes.md", ".github/workflows/README.md", "scripts/usage.md", "tests/NOTES.MD",  # .md hech qachon
        ".claude/agents/reviewer.md", ".claude/settings.json", ".claude/hooks_extra.py", "src/app.py", "src/main.ts", "lib/user.rb", "package.json",
        "docs/Makefile", "src/Makefile", "toolsx/a.py", "testing/a.py", "scripts.sh", "src/tests/a.py", "src/test_data.json", "src/user_test.rs",
        "src/a.spec.py", "web/specs.ts", "web/test.ts.bak", "i18n/en.json",
    )

    @classmethod
    def setUpClass(cls):
        cls.root = SCRATCH / "check-paths"
        cls.names = [*cls.ALLOWED, *cls.DENIED]
        make_repo(cls.root, {posixpath.normpath(name): "" for name in cls.names})
        rows = [row(category=f"p{i}.x", caught=f"c{i}", rule=f"`{name}`") for i, name in enumerate(cls.names)]
        errors = check(journal(rows), root=cls.root)
        cls.failed = {n - FIRST_FINDING_LINE for n, msg in errors if NAMED_ERROR in msg}
        cls.other = [(n, msg) for n, msg in errors if NAMED_ERROR not in msg]

    def test_only_named_check_errors_are_reported(self):
        self.assertEqual(self.other, [])

    def test_allowed_paths(self):
        for index, name in enumerate(self.names[:len(self.ALLOWED)]):
            with self.subTest(path=name):
                self.assertNotIn(index, self.failed)

    def test_denied_paths(self):
        for index, name in enumerate(self.names[len(self.ALLOWED):], start=len(self.ALLOWED)):
            with self.subTest(path=name):
                self.assertIn(index, self.failed)

    def test_default_check_paths_in_plain_words(self):
        # tests/ va scripts/ ostidagi fayl tekshiruv, docs/ ostidagi hujjat emas
        findings = [row(category="a.t", rule="`tests/x_test.py`"), row(category="a.s", rule="`scripts/check.sh`"), row(category="a.d", rule="`docs/x.md`")]
        errors = check(journal(findings))
        self.assertEqual([n for n, _ in errors], [FIRST_FINDING_LINE + 2])


class CheckPathConfigTest(RuleCase):
    """Config quality_log.check_prefixes / check_patterns / check_files standart ro'yxatni almashtiradi (to'ldirmaydi)."""

    def assertVerdicts(self, section, expected, root=None):
        """expected: {yo'l: tekshiruv deb qabul qilinishi kerakmi}, hammasi bitta ishga tushirishda."""
        found = rule_errors([f"`{path}`" for path in expected], config={"quality_log": section}, root=root)
        for (path, accepted), messages in zip(expected.items(), found):
            with self.subTest(path=path, section=section):
                self.assertEqual(not any(NAMED_ERROR in msg for msg in messages), accepted, messages)

    def test_default_prefixes_apply_without_config(self):
        self.assertVerdicts({}, {"tools/x.py": True, "qa/run.sh": False})

    def test_prefix_list_replaces_defaults(self):
        self.assertVerdicts({"check_prefixes": ["qa/", "ops/"]}, {
            "qa/run.sh": True, "ops/run.sh": True, "lab/run.sh": False,
            "tools/x.py": False,  # standart prefiks almashtirilgan
            "tests/x_test.py": True,  # nom andozasi (*_test.py) alohida ro'yxat: o'zgarmagan
        })

    def test_empty_prefix_list_means_no_prefixes(self):
        self.assertVerdicts({"check_prefixes": []}, {"tools/x.py": False, "tests/x_test.py": True})

    def test_pattern_list_replaces_defaults(self):
        self.assertVerdicts({}, {"src/util_test.py": True, "qa/run.sh": False})  # standart nom andozasi (*_test.py), prefiks ostida emas
        self.assertVerdicts({"check_patterns": ["run.*"]}, {
            "qa/run.sh": True,
            "src/util_test.py": False,  # standart andoza almashtirilgan
            "tools/x.py": True,  # prefiks ro'yxati almashtirilmagan
        })

    def test_pattern_with_slash_matches_full_path_otherwise_basename(self):
        self.assertVerdicts({"check_prefixes": [], "check_patterns": ["lab/*.sh"]}, {"lab/run.sh": True, "qa/run.sh": False})
        self.assertVerdicts({"check_prefixes": [], "check_patterns": ["run.sh"]}, {"qa/run.sh": True, "lab/run.sh": True, "tools/x.py": False})

    def test_replacing_both_lists_drops_all_defaults(self):
        self.assertVerdicts({"check_prefixes": [], "check_patterns": ["*.chk"]}, {"tests/x_test.py": False, "scripts/check.sh": False, "src/util_test.py": False})

    def test_files_list_replaces_defaults(self):
        root = SCRATCH / "check-files"
        make_repo(root, ["Makefile", "Justfile", "docs/Makefile", "build.sh"])
        self.addCleanup(shutil.rmtree, root, True)
        self.assertVerdicts({}, {"Makefile": True, "Justfile": False}, root)
        self.assertVerdicts({"check_files": ["Justfile"]}, {"Justfile": True, "Makefile": False}, root)
        self.assertVerdicts({"check_files": ["docs/Makefile", "./build.sh"]}, {"docs/Makefile": True, "build.sh": True, "Makefile": False}, root)

    def test_markdown_is_never_a_check_even_when_configured(self):
        section = {"check_prefixes": ["docs/"], "check_files": ["docs/status.md"], "check_patterns": ["*.md"]}
        self.assertVerdicts(section, {"docs/status.md": False, "docs/runbook/README.md": False})

    def test_error_message_names_configured_paths(self):
        message = rule_errors(["prose"], config={"quality_log": {"check_prefixes": ["qa/"], "check_patterns": ["*.chk"], "check_files": ["Justfile"]}})[0][0]
        for expected in ("qa/", "*.chk", "Justfile"):
            self.assertIn(expected, message)
        self.assertNotIn("tools/", message)


class SystemMinorTest(unittest.TestCase):
    """quality_log.minor_rule_from: shu sanadan boshlab tizim tomonidagi `minor` topilma jurnalda turmaydi (PR izohida qoladi)."""
    CUTOFF = "2026-11-05"
    LATER = ("2026-11-05", "2026-11-06", "2027-01-01")
    SELF = ("ci", "docs", "test", "review", "quality", "agent", "style", "arch", "git", "build", "spec", "dev", "config", "dup", "seed")
    PRODUCT = ("ui", "tenant", "access", "files", "time")
    NEEDLE = "jurnalga yozilmaydi"
    NOW = "2026-11-06"
    CONFIG = {"quality_log": {"minor_rule_from": CUTOFF}}
    L = FIRST_FINDING_LINE

    def errors(self, *rows, today=NOW, config=None):
        return check(journal(list(rows)), today, config=self.CONFIG if config is None else config)

    def limited(self, *rows, **kwargs):
        return [n for n, msg in self.errors(*rows, **kwargs) if self.NEEDLE in msg]

    def minor(self, category, date=CUTOFF, **kwargs):
        return row(date=date, category=category, severity="minor", **{"rule": "-", **kwargs})

    def test_system_minor_on_cutoff_date_is_red_with_message(self):
        errors = self.errors(self.minor("ci.x"))
        self.assertEqual([n for n, _ in errors], [self.L])
        for part in ("tizim kategoriyasi `ci.x`", "`minor`", "2026-11-05", "jurnalga yozilmaydi", "PR izohida qoldiring", "`major`/`blocker`"):
            self.assertIn(part, errors[0][1])

    def test_every_default_system_prefix_is_red_from_cutoff(self):
        combos = [(prefix, date) for prefix in self.SELF for date in self.LATER]
        errors = self.errors(*[self.minor(f"{prefix}.k{i}", date) for i, (prefix, date) in enumerate(combos)], today="2027-01-02")
        self.assertEqual([n for n, _ in errors], [self.L + i for i in range(len(combos))])
        for i, ((prefix, _date), (_n, message)) in enumerate(zip(combos, errors)):
            with self.subTest(prefix=prefix, i=i):
                self.assertIn(f"`{prefix}.k{i}`", message)
                self.assertIn(self.NEEDLE, message)

    def test_blocker_and_major_are_allowed_for_system_categories(self):
        combos = [(prefix, level) for prefix in self.SELF for level in ("blocker", "major")]
        self.assertEqual(self.errors(*[row(date=self.CUTOFF, category=f"{prefix}.k{i}", severity=level) for i, (prefix, level) in enumerate(combos)]), [])

    def test_product_categories_are_allowed_at_any_level(self):
        combos = [(prefix, level, date) for prefix in self.PRODUCT for level in ("blocker", "major", "minor") for date in self.LATER]
        rows = [row(date=date, category=f"{prefix}.k{i}", severity=level) for i, (prefix, level, date) in enumerate(combos)]
        self.assertEqual(self.errors(*rows, today="2027-01-02"), [])

    def test_prefix_is_the_whole_first_segment(self):
        # `startswith` yoki `in` bilan `testing.`, `devices.`, `archive.` kabi mahsulot kategoriyalari tizim deb qizil bo'lardi
        names = ["testing.x", "devices.x", "archive.x", "cinema.x", "gitlab.x", "builder.x"]
        names += [f"{prefix}x.y" for prefix in self.SELF] + [f"x{prefix}.y" for prefix in self.SELF]
        self.assertEqual(self.errors(*[self.minor(name) for name in names]), [])

    def test_system_name_after_the_first_dot_is_not_a_prefix(self):
        self.assertEqual(self.errors(*[self.minor(name) for name in ("ui.ci", "tenant.docs-sync", "access.test", "files.dev.seed")]), [])

    def test_rows_before_cutoff_are_grandfathered(self):
        combos = [(prefix, date) for prefix in self.SELF for date in ("2026-11-04", "2026-10-24", "2020-01-01")]
        self.assertEqual(self.errors(*[self.minor(f"{prefix}.k{i}", date) for i, (prefix, date) in enumerate(combos)]), [])

    def test_cutoff_depends_on_row_date_not_today(self):
        rows = [self.minor("ci.x", self.CUTOFF), self.minor("ci.y", "2026-11-04")]
        for today in ("2026-10-24", "2027-06-01"):
            with self.subTest(today=today):
                self.assertEqual(self.limited(*rows, today=today), [self.L])

    def test_unparseable_date_reports_only_the_date_error(self):
        bad_dates = ("2026-13-45", "2026-02-30", "05.11.2026", "2026-11-5", "")
        errors = self.errors(*[self.minor(f"ci.k{i}", bad) for i, bad in enumerate(bad_dates)])
        self.assertEqual([(n, "Date" in msg) for n, msg in errors], [(self.L + i, True) for i in range(len(bad_dates))])

    def test_unknown_level_reports_only_the_level_error(self):
        bad_levels = ("Minor", "MINOR", "critical", "")
        errors = self.errors(*[row(date=self.CUTOFF, category=f"ci.k{i}", severity=bad, rule="-") for i, bad in enumerate(bad_levels)])
        self.assertEqual([(n, "Severity" in msg) for n, msg in errors], [(self.L + i, True) for i in range(len(bad_levels))])

    def test_rule_column_does_not_excuse_the_row(self):
        rules = ("-", "`tools/x.py`", "accepted: one-off", "planned: session 60")
        self.assertEqual(self.limited(*[self.minor(f"ci.k{i}", rule=rule) for i, rule in enumerate(rules)]), [self.L + i for i in range(len(rules))])

    def test_only_the_offending_rows_are_flagged(self):
        rows = [row(date=self.CUTOFF, category="ui.a", severity="minor"), self.minor("ci.b"), row(date=self.CUTOFF, category="ci.c"),
                self.minor("docs.d", "2026-11-04"), self.minor("test.e", "2026-11-06")]
        self.assertEqual(self.limited(*rows), [self.L + 1, self.L + 4])
        self.assertEqual(len(self.errors(*rows)), 2)

    def test_limit_is_added_to_other_row_errors(self):
        errors = self.errors(self.minor("ci.x", pr="40"))
        self.assertEqual(len(errors), 2)
        self.assertTrue(any(msg.startswith("PR ") for _, msg in errors))
        self.assertTrue(any(self.NEEDLE in msg for _, msg in errors))

    def test_invalid_category_with_system_prefix_gets_both_errors(self):
        errors = self.errors(self.minor("ci"))
        self.assertEqual(len(errors), 2)
        self.assertTrue(any(msg.startswith("Category ") for _, msg in errors))
        self.assertTrue(any(self.NEEDLE in msg for _, msg in errors))

    def test_red_minor_row_still_counts_in_its_category_group(self):
        # qizil minor qator kategoriya hisobida qoladi: ikki qator + nomlangan qoida bo'lsa faqat minor xatosi chiqadi
        errors = self.errors(self.minor("ci.x"), row(date=self.CUTOFF, category="ci.x", severity="major"))
        self.assertEqual([n for n, _ in errors], [self.L])
        self.assertIn(self.NEEDLE, errors[0][1])

    def test_rule_is_off_without_minor_rule_from(self):
        rows = [self.minor("ci.flaky-timeout", "2026-11-05")]
        for config in ({}, {"quality_log": {}}, {"quality_log": {"minor_rule_from": None}}):
            with self.subTest(config=config):
                self.assertEqual(self.errors(*rows, config=config), [])

    def test_configured_system_prefixes_replace_defaults(self):
        config = {"quality_log": {"minor_rule_from": self.CUTOFF, "system_prefixes": ["billing", "infra"]}}
        self.assertEqual(self.limited(self.minor("billing.rounding"), self.minor("infra.disk"), self.minor("ci.flaky"), self.minor("ui.corner"), config=config), [self.L, self.L + 1])

    def test_cutoff_date_comes_from_config(self):
        rows = [self.minor("ci.x", "2026-11-05")]
        for since, expected in (("2026-11-05", [self.L]), ("2026-11-06", []), ("2026-01-01", [self.L])):
            with self.subTest(minor_rule_from=since):
                self.assertEqual(self.limited(*rows, config={"quality_log": {"minor_rule_from": since}}), expected)

    def test_invalid_minor_rule_from_is_reported_and_disables_the_rule(self):
        path = SCRATCH / "quality-log.md"
        path.write_text(journal([self.minor("ci.x")]), encoding="utf-8")
        for bad in ("yesterday", "2026-13-01", 20261105):
            with self.subTest(value=bad):
                result = run_cli("--check", "--file", str(path), "--root", str(FIXTURE_ROOT), "--today", self.NOW, config={"quality_log": {"minor_rule_from": bad}})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("minor_rule_from", result.stderr)

    def test_command_line_reports_the_row_with_file_line(self):
        path = SCRATCH / "quality-log.md"
        path.write_text(journal([self.minor("ci.x")]), encoding="utf-8")
        red = run_cli("--check", "--file", str(path), "--root", str(FIXTURE_ROOT), "--today", self.CUTOFF, config=self.CONFIG)
        path.write_text(journal([row(date=self.CUTOFF, category="ci.x", severity="major", rule="-")]), encoding="utf-8")
        green = run_cli("--check", "--file", str(path), "--root", str(FIXTURE_ROOT), "--today", self.CUTOFF, config=self.CONFIG)
        self.assertEqual(red.returncode, 1, red.stdout + red.stderr)
        self.assertIn(f"quality-log.md:{self.L}:", red.stderr)
        self.assertIn(self.NEEDLE, red.stderr)
        self.assertEqual(green.returncode, 0, green.stdout + green.stderr)


class GateStageConfigTest(RuleCase):
    def test_gate_script_is_relative_to_root_and_may_be_absolute(self):
        self.assertAccepted(["`canary`"], config=GATE_CONFIG)
        self.assertAccepted(["`canary`"], config={"quality_log": {"gate_script": str(FIXTURE_ROOT / "ci" / "gate.sh")}})

    def test_stage_line_forms(self):
        root = SCRATCH / "stages"
        script = "stage a1 . x\n\tstage b-2 . x\n  stage c.3 .\nstage d4\nstage\ne5 . x\nxstage f6 . x\n# stage g7 . x\nstage() { :; }\nstage _h8 . x\n"
        make_repo(root, {"gate.sh": script})
        self.addCleanup(shutil.rmtree, root, True)
        expected = {"a1": True, "b-2": True, "c.3": True, "d4": True, "e5": False, "f6": False, "g7": False, "_h8": False}
        found = rule_errors([f"`{name}`" for name in expected], root=root, config={"quality_log": {"gate_script": "gate.sh"}})
        for (name, accepted), messages in zip(expected.items(), found):
            with self.subTest(stage=name):
                self.assertEqual(not any(NAMED_ERROR in msg for msg in messages), accepted, messages)


class ConfigResolutionTest(unittest.TestCase):
    ROWS = ("qa", "ops", "lab", "tools")  # har qator bitta prefiksni sinaydi: qa/ (skript yonidagi config), ops/ (env), lab/ (--config)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="quality-log-cfg-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.script_dir = self.tmp / "installed"
        self.script_dir.mkdir()
        self.script = self.script_dir / "quality_log.py"
        shutil.copy2(SCRIPT, self.script)
        self.journal = self.tmp / "quality-log.md"
        rows = [row(category=f"{p}.x", caught=f"c-{p}", rule=f"`{p}/run.sh`") for p in self.ROWS]
        self.journal.write_text(journal(rows), encoding="utf-8")

    def write(self, name, data):
        path = self.tmp / name
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def prefixes(self, *names):
        return {"quality_log": {"check_prefixes": list(names)}}

    def passing(self, result):
        """Qaysi qatorlar (qa, ops, lab, tools) qizil emas."""
        failed = {n for n, _ in parse_errors(result.stderr)}
        return [p for i, p in enumerate(self.ROWS) if FIRST_FINDING_LINE + i not in failed]

    def run_script(self, *extra, env_config=None):
        """Skript nusxasi (o'rnatilgan joylashuv); QUALITY_CONFIG faqat env_config berilganda."""
        env = dict(ENV)
        if env_config:
            env["QUALITY_CONFIG"] = str(env_config)
        return subprocess.run([sys.executable, str(self.script), "--check", "--file", str(self.journal), "--root", str(FIXTURE_ROOT), "--today", TODAY_ISO, *extra],
                              capture_output=True, text=True, env=env)

    def test_defaults_when_no_config_anywhere(self):
        result = self.run_script()
        self.assertEqual(self.passing(result), ["tools"], result.stderr)

    def test_config_next_to_the_script_is_used(self):
        (self.script_dir / "config.json").write_text(json.dumps(self.prefixes("qa/")))
        result = self.run_script()
        self.assertEqual(self.passing(result), ["qa"], result.stderr)

    def test_env_beats_config_next_to_the_script(self):
        (self.script_dir / "config.json").write_text(json.dumps(self.prefixes("qa/")))
        result = self.run_script(env_config=self.write("env.json", self.prefixes("ops/")))
        self.assertEqual(self.passing(result), ["ops"], result.stderr)

    def test_argument_beats_env_and_config_next_to_the_script(self):
        (self.script_dir / "config.json").write_text(json.dumps(self.prefixes("qa/")))
        result = self.run_script("--config", str(self.write("arg.json", self.prefixes("lab/"))), env_config=self.write("env.json", self.prefixes("ops/")))
        self.assertEqual(self.passing(result), ["lab"], result.stderr)

    def test_missing_config_file_is_silent_defaults(self):
        result = self.run_script("--config", str(self.tmp / "nope.json"))
        self.assertEqual(self.passing(result), ["tools"], result.stderr)
        self.assertNotIn("o'qilmadi", result.stderr)

    def test_unreadable_config_warns_once_and_uses_defaults(self):
        for text in ("{not json", "[]", '"text"', ""):
            with self.subTest(text=text):
                broken = self.tmp / "broken.json"
                broken.write_text(text)
                result = self.run_script("--config", str(broken))
                self.assertEqual(self.passing(result), ["tools"], result.stderr)
                notes = [line for line in result.stderr.splitlines() if line.startswith("quality_log: ")]
                self.assertEqual(len(notes), 1, result.stderr)
                self.assertIn("broken.json", notes[0])

    def test_directory_as_config_is_unreadable_not_a_crash(self):
        result = self.run_script("--config", str(self.tmp))
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(self.passing(result), ["tools"], result.stderr)

    def test_missing_keys_use_defaults(self):
        for data in ({"ref": "origin/main", "quality_log": {}}, {"protected": ["main"]}):
            with self.subTest(config=data):
                (self.script_dir / "config.json").write_text(json.dumps(data))
                self.assertEqual(self.passing(self.run_script()), ["tools"])

    def test_invalid_values_warn_and_use_defaults(self):
        config = {"quality_log": {"check_prefixes": "qa/", "check_patterns": [1], "check_files": [""], "system_prefixes": "ci", "gate_script": 5, "file": ""}}
        result = self.run_script("--config", str(self.write("bad-values.json", config)))
        self.assertEqual(self.passing(result), ["tools"], result.stderr)
        for key in ("check_prefixes", "check_patterns", "check_files", "system_prefixes", "gate_script", "file"):
            self.assertIn(f"quality_log.{key}", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_quality_log_section_of_wrong_type_is_reported(self):
        result = self.run_script("--config", str(self.write("section.json", {"quality_log": ["x"]})))
        self.assertEqual(self.passing(result), ["tools"], result.stderr)
        self.assertIn("quality_log", result.stderr)


class JournalPathTest(unittest.TestCase):
    """--file > config quality_log.file > docs/quality-log.md (ildizga nisbatan)."""

    def setUp(self):
        self.root = SCRATCH / "journal-path"
        shutil.rmtree(self.root, ignore_errors=True)
        make_repo(self.root, {"tools/x.py": ""})
        self.addCleanup(shutil.rmtree, self.root, True)

    def put(self, relative, count):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(journal([row(category=f"a.k{i}", caught=f"c{i}", rule="-") for i in range(count)]), encoding="utf-8")
        return path

    def findings(self, *args, config=None):
        result = run_cli("--check", "--root", str(self.root), "--today", TODAY_ISO, *args, config=config)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return int(re.search(r"(\d+) findings", result.stdout).group(1))

    def test_default_is_docs_quality_log_under_root(self):
        self.put("docs/quality-log.md", 1)
        self.put("other.md", 5)
        self.assertEqual(self.findings(), 1)

    def test_config_file_beats_default_and_is_relative_to_root(self):
        self.put("docs/quality-log.md", 1)
        self.put("custom/log.md", 2)
        self.assertEqual(self.findings(config={"quality_log": {"file": "custom/log.md"}}), 2)

    def test_config_file_may_be_absolute(self):
        self.put("docs/quality-log.md", 1)
        path = self.put("elsewhere/log.md", 4)
        self.assertEqual(self.findings(config={"quality_log": {"file": str(path)}}), 4)

    def test_argument_beats_config_and_default(self):
        self.put("docs/quality-log.md", 1)
        self.put("custom/log.md", 2)
        explicit = self.put("explicit.md", 3)
        self.assertEqual(self.findings("--file", str(explicit), config={"quality_log": {"file": "custom/log.md"}}), 3)

    def test_missing_default_file_is_red_and_names_it(self):
        result = run_cli("--check", "--root", str(self.root), "--today", TODAY_ISO)
        self.assertEqual(result.returncode, 1)
        self.assertIn("o'qilmadi", result.stderr)
        self.assertIn("quality-log.md", result.stderr)

    def test_report_shows_the_path_relative_to_root(self):
        path = self.root / "docs" / "quality-log.md"
        path.parent.mkdir(parents=True)
        path.write_text(journal([row(severity="x", rule="-")]), encoding="utf-8")
        result = run_cli("--check", "--root", str(self.root), "--today", TODAY_ISO)
        self.assertEqual(result.returncode, 1)
        self.assertIn("quality_log QIZIL: docs/quality-log.md da 1 muammo", result.stderr)
        self.assertIn(f"docs/quality-log.md:{FIRST_FINDING_LINE}:", result.stderr)


class RootTest(unittest.TestCase):
    """--root bo'lmasa: skript papkasining git ildizi; git repo bo'lmasa joriy papka."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="quality-log-root-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.ceiling = {"GIT_CEILING_DIRECTORIES": os.path.realpath(self.tmp)}  # git ota papkalardan repo qidirmasin

    def project(self, rule="`tools/x.py`"):
        root = make_repo(self.tmp / "project", {"tools/x.py": ""})
        log = root / "docs" / "quality-log.md"
        log.parent.mkdir(parents=True)
        log.write_text(journal([row(rule=rule)]), encoding="utf-8")
        return root

    def installed_script(self, root):
        script_dir = root / ".claude" / "quality"  # o'rnatilgan joylashuv
        script_dir.mkdir(parents=True)
        shutil.copy2(SCRIPT, script_dir / "quality_log.py")
        return script_dir / "quality_log.py"

    def test_default_root_is_git_toplevel_of_the_script_dir(self):
        script = self.installed_script(self.project())
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        result = run_cli("--check", "--today", TODAY_ISO, cwd=elsewhere, script=script)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("1 findings", result.stdout)

    def test_default_root_resolves_named_checks_in_that_repo(self):
        script = self.installed_script(self.project(rule="`tools/missing.py`"))
        result = run_cli("--check", "--today", TODAY_ISO, cwd=self.tmp, script=script)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(NAMED_ERROR, result.stderr)

    def test_default_root_falls_back_to_current_directory(self):
        root = self.project()
        plain = self.tmp / "plain"  # git repo emas
        plain.mkdir()
        shutil.copy2(SCRIPT, plain / "quality_log.py")
        result = run_cli("--check", "--today", TODAY_ISO, cwd=root, script=plain / "quality_log.py", env_extra=self.ceiling)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("1 findings", result.stdout)

    def test_explicit_root_beats_the_default(self):
        root = self.project()
        plain = self.tmp / "plain"
        plain.mkdir()
        shutil.copy2(SCRIPT, plain / "quality_log.py")
        result = run_cli("--check", "--today", TODAY_ISO, "--root", str(root), cwd=self.tmp, script=plain / "quality_log.py", env_extra=self.ceiling)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class CanaryTableTest(unittest.TestCase):
    def errors(self, rows):
        return check(journal(canary=rows))

    def canary_line(self, index=0):
        # Findings bo'sh bo'lganda canary qatorlari 15-satrdan boshlanadi
        return 15 + index

    def test_valid_rows(self):
        self.assertEqual(self.errors([canary_row(), canary_row(planted="5", caught="5", rate="5/5"), canary_row(planted="3", caught="0", rate="0/3")]), [])

    def test_section_is_optional(self):
        self.assertEqual(check(journal([row()], with_canary=False)), [])

    def test_heading_without_table_is_error(self):
        errors = check(journal([row()], with_canary=False) + "\n## Critic canary\nno table here\n")
        self.assertEqual(len(errors), 1)
        self.assertIn("jadval yo'q", errors[0][1])

    def test_caught_greater_than_planted(self):
        errors = self.errors([canary_row(planted="3", caught="4", rate="4/3")])
        self.assertEqual([n for n, _ in errors], [self.canary_line()])
        self.assertIn("katta bo'lishi mumkin emas", errors[0][1])

    def test_non_integers(self):
        errors = self.errors([canary_row(planted="three", caught="3.5", rate="3/4")])
        self.assertTrue(any("Planted" in m for _, m in errors))
        self.assertTrue(any("Caught" in m for _, m in errors))

    def test_rate_must_match(self):
        errors = self.errors([canary_row(planted="4", caught="3", rate="2/4")])
        self.assertEqual(len(errors), 1)
        self.assertIn("3/4", errors[0][1])

    def test_rate_format(self):
        bad_rates = ("75%", "0.75", "3 / 4", "3/", "")
        errors = self.errors([canary_row(rate=bad) for bad in bad_rates])
        for i, bad in enumerate(bad_rates):
            with self.subTest(rate=bad):
                self.assertTrue(any(n == self.canary_line(i) and "Rate" in m and "N/M" in m for n, m in errors), errors)

    def test_line_number_of_second_row(self):
        errors = self.errors([canary_row(), canary_row(rate="1/4")])
        self.assertEqual([n for n, _ in errors], [self.canary_line(1)])

    def test_wrong_header(self):
        self.assertTrue(any("sarlavhasi" in m for _, m in check(journal(canary_header="| Date | Branch |"))))

    def test_old_uzbek_canary_header_is_rejected(self):
        header = "| Sana | Branch | Ekilgan | Topildi | O'tib ketdi (kategoriya) | Ulush | Izoh |"
        self.assertTrue(any("sarlavhasi" in m for _, m in check(journal(canary_header=header))))


class CommandLineTest(unittest.TestCase):
    def test_script_is_executable_python3(self):
        self.assertTrue(os.access(SCRIPT, os.X_OK), f"{SCRIPT} bajariladigan emas")
        self.assertEqual(SCRIPT.read_text(encoding="utf-8").splitlines()[0], "#!/usr/bin/env python3")

    def test_green_output_counts_and_date(self):
        path = SCRATCH / "quality-log.md"
        path.write_text(journal([row(), row(category="a.b", severity="minor", rule="-")], canary=[canary_row()]), encoding="utf-8")
        result = run_cli("--check", "--file", str(path), "--root", str(FIXTURE_ROOT), "--today", TODAY_ISO)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count("\n"), 1)
        self.assertIn("quality_log: 2 findings, 1 canary rows", result.stdout)
        self.assertIn(TODAY_ISO, result.stdout)

    def test_default_today_is_the_system_date(self):
        path = SCRATCH / "quality-log.md"
        path.write_text(journal([row(rule="-")]), encoding="utf-8")
        result = run_cli("--check", "--file", str(path), "--root", str(FIXTURE_ROOT))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout, r"\(\d{4}-\d{2}-\d{2}\)")

    def test_requires_check_flag(self):
        self.assertEqual(run_cli().returncode, 2)

    def test_bad_today(self):
        for bad in ("24.10.2026", "2026-13-01"):
            with self.subTest(today=bad):
                self.assertEqual(run_cli("--check", "--today", bad).returncode, 2)

    def test_help_prints_the_format_specification(self):
        result = run_cli("--help")
        self.assertEqual(result.returncode, 0)
        for expected in ("## Findings", "Date | PR | Category | Severity | Caught by | Rule", "planned:", "accepted:", "minor_rule_from", "--config"):
            self.assertIn(expected, result.stdout)

    def test_red_report_has_file_and_line_numbers(self):
        path = SCRATCH / "quality-log.md"
        path.write_text(journal([row(), row(severity="x", rule="-")]))
        result = run_cli("--check", "--file", str(path), "--root", str(FIXTURE_ROOT), "--today", TODAY_ISO)
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, out)
        self.assertEqual(result.stdout, "")  # qizil hisobot stderr ga
        self.assertIn("quality_log QIZIL:", result.stderr)
        self.assertIn("1 muammo", result.stderr)
        self.assertIn(f"quality-log.md:{FIRST_FINDING_LINE + 1}:", out)
        self.assertIn("Severity", out)

    def test_recurrence_report_has_line_numbers(self):
        path = SCRATCH / "quality-log.md"
        path.write_text(journal([row(rule="accepted: x"), row(rule="-"), row(rule="-")]))
        result = run_cli("--check", "--file", str(path), "--root", str(FIXTURE_ROOT), "--today", TODAY_ISO)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(f"quality-log.md:{FIRST_FINDING_LINE + 2}:", result.stderr)

    def test_missing_section_report_has_no_line_number(self):
        path = SCRATCH / "quality-log.md"
        path.write_text("# Quality log\n")
        result = run_cli("--check", "--file", str(path), "--root", str(FIXTURE_ROOT), "--today", TODAY_ISO)
        self.assertEqual(result.returncode, 1)
        self.assertRegex(result.stderr, r"quality-log\.md: `## Findings` bo'limi topilmadi")

    def test_missing_file(self):
        result = run_cli("--check", "--file", "/nonexistent/quality-log.md", "--root", str(FIXTURE_ROOT))
        self.assertEqual(result.returncode, 1)
        self.assertIn("o'qilmadi", result.stderr)

    def test_not_utf8_file_is_red_not_a_crash(self):
        path = SCRATCH / "binary.md"
        path.write_bytes(b"\xff\xfe\x00bad")
        result = run_cli("--check", "--file", str(path), "--root", str(FIXTURE_ROOT))
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("Traceback", result.stderr)

    def test_no_traceback_on_odd_input(self):
        for text in ("", "## Findings\n|", "## Findings\n| a |\n", "## Critic canary\n| x |\n"):
            with self.subTest(text=text):
                path = SCRATCH / "quality-log.md"
                path.write_text(text)
                result = run_cli("--check", "--file", str(path), "--root", str(FIXTURE_ROOT), "--today", TODAY_ISO)
                self.assertNotIn("Traceback", result.stderr)
                self.assertIn(result.returncode, (0, 1))


if __name__ == "__main__":
    unittest.main()
