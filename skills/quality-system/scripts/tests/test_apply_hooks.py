"""Tests for apply_hooks.py: the merge logic with synthetic rules, and the shipped references/settings-hooks.json as it is run for real.

  python3 -m unittest discover -s scripts/tests -v      (in the skill directory)
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APPLY = os.path.join(SCRIPTS, "apply_hooks.py")
UPDATE_CHECK = os.path.join(SCRIPTS, "update_check.py")
SHIPPED = os.path.join(os.path.dirname(SCRIPTS), "references", "settings-hooks.json")
SHIPPED_COMMAND = 'python3 "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills/quality-system/scripts/update_check.py" --hook'
RULES = {"hooks": {
    "SessionEnd": [{"hooks": [{"type": "command", "command": "hook-a", "timeout": 20}]}],
    "PostToolUse": [{"matcher": "^Agent$", "hooks": [{"type": "command", "command": "hook-a", "timeout": 20}]}],
    "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "hook-b"}]}],
}}
LINTER = {"matcher": "Bash", "hooks": [{"type": "command", "command": "my-linter"}]}


class ApplyHooksTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rules = os.path.join(self.tmp.name, "rules.json")
        self.settings = os.path.join(self.tmp.name, "settings.json")
        self.write(self.rules, RULES)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, path, data):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def read(self):
        with open(self.settings, encoding="utf-8") as f:
            return json.load(f)

    def raw(self):
        with open(self.settings, "rb") as f:
            return f.read()

    def run_apply(self, *args):
        # CLAUDE_CONFIG_DIR points into the temp dir, so a run that forgets --settings still cannot touch the real settings.json
        return subprocess.run([sys.executable, APPLY] + list(args), capture_output=True, text=True,
                              env=dict(os.environ, CLAUDE_CONFIG_DIR=self.tmp.name, PYTHONDONTWRITEBYTECODE="1"))

    def apply(self, *extra):
        return self.run_apply("--settings", self.settings, "--rules", self.rules, *extra)

    def test_adds_all_three_events_to_an_empty_settings_file(self):
        self.write(self.settings, {})
        r = self.apply()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("added 3 hook group(s), 0 already present", r.stdout)
        for line in ("+ SessionEnd (no matcher): hook-a", "+ PostToolUse (matcher ^Agent$): hook-a", "+ UserPromptSubmit (no matcher): hook-b"):
            self.assertIn(line, r.stdout)
        self.assertEqual(self.read(), RULES)

    def test_a_missing_settings_file_is_created(self):
        r = self.apply()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.read(), RULES)

    def test_a_missing_settings_directory_is_created_too(self):
        self.settings = os.path.join(self.tmp.name, "new", "dir", "settings.json")
        r = self.apply()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.read(), RULES)

    def test_rerun_adds_nothing_and_leaves_the_file_byte_identical(self):
        self.write(self.settings, {"theme": "dark"})
        self.apply()
        before = self.raw()
        r = self.apply()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("added 0 hook group(s), 3 already present", r.stdout)
        self.assertIn("= PostToolUse (matcher ^Agent$): hook-a  (already present)", r.stdout)
        self.assertEqual(self.raw(), before)

    def test_nothing_to_add_means_the_file_is_not_even_reformatted(self):
        self.write(self.settings, RULES)  # one line, not the indent=2 layout the script writes
        before = self.raw()
        r = self.apply()
        self.assertIn("added 0 hook group(s), 3 already present", r.stdout)
        self.assertEqual(self.raw(), before)

    def test_a_group_with_other_commands_is_not_the_same_group(self):
        self.write(self.settings, {"hooks": {"SessionEnd": [{"hooks": [{"type": "command", "command": "somebody-elses-hook"}]}]}})
        r = self.apply()
        self.assertIn("added 3 hook group(s), 0 already present", r.stdout)
        self.assertEqual(len(self.read()["hooks"]["SessionEnd"]), 2)

    def test_keeps_an_unrelated_hook_group_and_every_other_key_in_order(self):
        self.write(self.settings, {"theme": "dark", "hooks": {"PostToolUse": [LINTER], "Stop": [{"hooks": [{"type": "command", "command": "bye"}]}]},
                                   "permissions": {"deny": ["Bash(rm -rf /)"]}})
        r = self.apply()
        self.assertIn("added 3 hook group(s)", r.stdout)
        s = self.read()
        self.assertEqual(s["hooks"]["PostToolUse"], [LINTER, RULES["hooks"]["PostToolUse"][0]])  # appended after the one that was there
        self.assertEqual(s["hooks"]["Stop"], [{"hooks": [{"type": "command", "command": "bye"}]}])
        self.assertEqual(s["permissions"], {"deny": ["Bash(rm -rf /)"]})
        self.assertEqual(s["theme"], "dark")
        self.assertEqual(list(s), ["theme", "hooks", "permissions"])
        self.assertEqual(list(s["hooks"]), ["PostToolUse", "Stop", "SessionEnd", "UserPromptSubmit"])

    def test_the_same_command_under_another_matcher_is_another_group(self):
        self.write(self.settings, {"hooks": {"PostToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "hook-a"}]}]}})
        r = self.apply()
        self.assertIn("added 3 hook group(s)", r.stdout)
        self.assertEqual(len(self.read()["hooks"]["PostToolUse"]), 2)

    def test_an_existing_group_counts_when_matcher_and_commands_match_whatever_else_differs(self):
        self.write(self.settings, {"hooks": {
            "PostToolUse": [{"hooks": [{"command": "hook-a", "type": "command", "timeout": 5}], "matcher": "^Agent$"}],
            "SessionEnd": [{"hooks": [{"type": "command", "command": "hook-a"}]}, {"hooks": [{"type": "command", "command": "hook-b"}]}]}})
        r = self.apply()
        self.assertIn("added 1 hook group(s), 2 already present", r.stdout)
        s = self.read()
        self.assertEqual(len(s["hooks"]["PostToolUse"]), 1)
        self.assertEqual(len(s["hooks"]["SessionEnd"]), 2)
        self.assertEqual(s["hooks"]["UserPromptSubmit"], RULES["hooks"]["UserPromptSubmit"])

    def test_an_empty_or_star_matcher_is_the_same_as_none(self):
        self.write(self.settings, {"hooks": {"SessionEnd": [{"matcher": "", "hooks": [{"type": "command", "command": "hook-a"}]}],
                                             "UserPromptSubmit": [{"matcher": "*", "hooks": [{"type": "command", "command": "hook-b"}]}]}})
        r = self.apply()
        self.assertIn("added 1 hook group(s), 2 already present", r.stdout)
        s = self.read()
        self.assertEqual((len(s["hooks"]["SessionEnd"]), len(s["hooks"]["UserPromptSubmit"]), len(s["hooks"]["PostToolUse"])), (1, 1, 1))

    def test_dry_run_writes_nothing(self):
        r = self.apply("--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("would add 3 hook group(s)", r.stdout)
        self.assertIn("(dry run, nothing written)", r.stdout)
        self.assertFalse(os.path.exists(self.settings))
        self.write(self.settings, {"theme": "dark"})
        before = self.raw()
        self.apply("--dry-run")
        self.assertEqual(self.raw(), before)

    def test_broken_settings_file_is_refused(self):
        with open(self.settings, "w", encoding="utf-8") as f:
            f.write("{not json")
        r = self.apply()
        self.assertEqual(r.returncode, 1)
        self.assertIn("not valid JSON", r.stderr)
        self.assertEqual(self.raw(), b"{not json")

    def test_settings_of_the_wrong_shape_are_refused_untouched(self):
        for bad in ({"hooks": []}, {"hooks": {"PostToolUse": {}}}, {"hooks": None}, ["not", "an", "object"]):
            self.write(self.settings, bad)
            before = self.raw()
            r = self.apply()
            self.assertEqual(r.returncode, 1, bad)
            self.assertIn("nothing was changed", r.stderr)
            self.assertEqual(self.raw(), before)

    def test_rules_of_the_wrong_shape_are_refused(self):
        for bad in ({"permissions": {}}, {"hooks": []}, {"hooks": {"Stop": [{"matcher": "x"}]}}, {"hooks": {"Stop": [{"hooks": ["cmd"]}]}}, []):
            self.write(self.rules, bad)
            r = self.apply()
            self.assertEqual(r.returncode, 1, bad)
            self.assertIn("expected", r.stderr)
            self.assertFalse(os.path.exists(self.settings))

    def test_a_missing_rules_file_is_refused(self):
        r = self.run_apply("--settings", self.settings, "--rules", os.path.join(self.tmp.name, "gone.json"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("cannot read", r.stderr)
        self.assertFalse(os.path.exists(self.settings))

    # the shipped references/settings-hooks.json

    def shipped(self):
        with open(SHIPPED, encoding="utf-8") as f:
            return json.load(f)

    def test_the_shipped_rules_file_merges_into_the_default_settings_file(self):
        r = self.run_apply()  # no --settings: $CLAUDE_CONFIG_DIR/settings.json; no --rules: references/settings-hooks.json
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("added 1 hook group(s), 0 already present", r.stdout)
        self.assertEqual(self.read(), self.shipped())

    def test_the_shipped_rules_are_one_session_start_group_for_the_update_check(self):
        shipped = self.shipped()
        self.assertEqual(list(shipped), ["hooks"])
        self.assertEqual(list(shipped["hooks"]), ["SessionStart"])
        groups = shipped["hooks"]["SessionStart"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["matcher"], "startup|clear")  # a regex over the SessionStart source; update_check.py --hook acts on exactly these two
        self.assertEqual(groups[0]["hooks"], [{"type": "command", "command": SHIPPED_COMMAND, "timeout": 10}])

    def test_the_shipped_rules_are_added_once_to_a_settings_file_that_has_other_hooks(self):
        self.write(self.settings, {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "mine"}]}]}, "theme": "dark"})
        self.run_apply("--settings", self.settings)
        r = self.run_apply("--settings", self.settings)
        self.assertIn("added 0 hook group(s), 1 already present", r.stdout)
        s = self.read()
        self.assertEqual(len(s["hooks"]["SessionStart"]), 2)
        self.assertEqual(s["hooks"]["SessionStart"][0]["hooks"][0]["command"], "mine")
        self.assertEqual(s["hooks"]["SessionStart"][1], self.shipped()["hooks"]["SessionStart"][0])
        self.assertEqual(s["theme"], "dark")

    def test_dry_run_of_the_shipped_rules_names_the_group_and_writes_nothing(self):
        missing = os.path.join(self.tmp.name, "nonexistent-settings.json")
        r = self.run_apply("--dry-run", "--settings", missing)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("+ SessionStart (matcher startup|clear): " + SHIPPED_COMMAND, r.stdout)
        self.assertIn("would add 1 hook group(s), 0 already present", r.stdout)
        self.assertFalse(os.path.exists(missing))

    def test_the_shipped_command_runs_as_written_from_either_config_location(self):
        event = json.dumps({"hook_event_name": "SessionStart", "source": "startup"})
        base = {k: v for k, v in os.environ.items() if k != "CLAUDE_CONFIG_DIR"}
        explicit, home = (os.path.join(self.tmp.name, name) for name in ("explicit", "home"))
        for config, env in ((explicit, dict(base, CLAUDE_CONFIG_DIR=explicit)), (os.path.join(home, ".claude"), dict(base, HOME=home))):
            scripts = os.path.join(config, "skills", "quality-system", "scripts")
            os.makedirs(scripts)
            shutil.copy(UPDATE_CHECK, scripts)
            # a run that reached the script ends silently (no install record); a wrong path would print python's "can't open file"
            p = subprocess.run(SHIPPED_COMMAND, shell=True, input=event, capture_output=True, text=True,
                               env=dict(env, PYTHONDONTWRITEBYTECODE="1"))
            self.assertEqual((p.returncode, p.stdout, p.stderr), (0, "", ""), config)


if __name__ == "__main__":
    unittest.main()
