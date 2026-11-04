#!/usr/bin/env python3
"""Merge the quality-system skill's update-check hook into a Claude Code settings.json: adds the missing hook groups, changes nothing else.

  apply_hooks.py --dry-run     # show what would be added to ~/.claude/settings.json, write nothing
  apply_hooks.py               # merge references/settings-hooks.json into ~/.claude/settings.json
  apply_hooks.py --settings ./.claude/settings.json --rules my-hooks.json

Defaults: settings = $CLAUDE_CONFIG_DIR/settings.json (else ~/.claude/settings.json); rules = ../references/settings-hooks.json,
a file shaped {"hooks": {"<Event>": [{"matcher": "...", "hooks": [{"type": "command", "command": "..."}]}]}}. A group is appended
to settings["hooks"]["<Event>"] unless that list already has a group with the same matcher (none equals none; "" and "*" mean
"everything" and count as none) and the same set of hook commands. Every other key and the key order of the settings file are
kept. A settings file that is not valid JSON is never overwritten (exit 1).
"""
import argparse
import json
import os
import sys


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except ValueError as e:
        sys.exit("%s is not valid JSON (%s); fix it by hand, nothing was changed" % (path, e))
    except OSError as e:
        sys.exit("cannot read %s: %s" % (path, e))


def groups_ok(groups):
    return isinstance(groups, list) and all(
        isinstance(g, dict) and isinstance(g.get("hooks"), list) and all(isinstance(h, dict) for h in g["hooks"]) for g in groups)


def same_as(group):
    """What makes two hook groups the same: the matcher (None for none, "" or "*": all) and the set of hook commands."""
    matcher = group.get("matcher")
    return None if matcher in ("", "*") else matcher, frozenset(str(h.get("command")) for h in group.get("hooks") or [] if isinstance(h, dict))


def label(event, group):
    matcher = "matcher %s" % group["matcher"] if group.get("matcher") is not None else "no matcher"
    return "%s (%s): %s" % (event, matcher, " ; ".join(str(h.get("command")) for h in group["hooks"]))


def main():
    config = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--settings", default=os.path.join(config, "settings.json"), help="settings file to update (default %(default)s)")
    ap.add_argument("--rules", default=os.path.normpath(os.path.join(here, "..", "references", "settings-hooks.json")),
                    help="JSON file with the hooks (default %(default)s)")
    ap.add_argument("--dry-run", action="store_true", help="print what would be added, write nothing")
    args = ap.parse_args()

    wanted = read_json(args.rules)
    wanted = wanted.get("hooks") if isinstance(wanted, dict) else None
    if not isinstance(wanted, dict) or not all(groups_ok(groups) for groups in wanted.values()):
        sys.exit('%s: expected {"hooks": {"<Event>": [{"matcher": "...", "hooks": [{"type": "command", "command": "..."}]}]}}'
                 % args.rules)

    settings = read_json(args.settings) if os.path.exists(args.settings) else {}  # a missing file is an empty one
    if not isinstance(settings, dict):
        sys.exit("%s: expected a JSON object at the top level; nothing was changed" % args.settings)
    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict) or not all(isinstance(groups, list) for groups in hooks.values()):
        sys.exit("%s: hooks is not an object of lists; nothing was changed" % args.settings)

    print(args.settings + ("  (dry run, nothing written)" if args.dry_run else ""))
    added = present = 0
    for event, groups in wanted.items():
        have = [same_as(g) for g in hooks.get(event, []) if isinstance(g, dict)]
        for group in groups:
            if same_as(group) in have:
                present += 1
                print("  = %s  (already present)" % label(event, group))
            else:
                hooks.setdefault(event, []).append(group)
                have.append(same_as(group))
                added += 1
                print("  + " + label(event, group))
    print("%s %d hook group(s), %d already present" % ("would add" if args.dry_run else "added", added, present))
    if added and not args.dry_run:
        text = json.dumps(settings, indent=2, ensure_ascii=False) + "\n"  # serialize first: a failure must not truncate the file
        os.makedirs(os.path.dirname(os.path.abspath(args.settings)), exist_ok=True)
        with open(args.settings, "w", encoding="utf-8") as f:
            f.write(text)


if __name__ == "__main__":
    main()
