#!/usr/bin/env bash
# quality-system skill'ini serverlar o'rnatadigan install repo'ga nashr qiladi (faqat skills/quality-system, agentsiz).
#
#   publish.sh [--skip-tests] ["commit xabari"]      standart xabar: "sync from ~/.claude <YYYY-MM-DD>"
#
# Avval uchta to'plam testini ishga tushiradi (scripts/tests, scripts/git-guard/tests, scripts/review-gate/tests): qizil bo'lsa
# nashr qilinmaydi, papkasi yo'q to'plam eslatma bilan o'tkaziladi, --skip-tests hammasini o'tkazib yuboradi. Keyin repo'ni
# (depth 1) vaqtinchalik papkaga clone qiladi, skills/quality-system ni $CLAUDE_CONFIG_DIR (standart ~/.claude) dan unga
# ko'zgulaydi va o'zgarish bo'lsa commit va push qiladi. Repo manzilini QUALITY_SYSTEM_REPO_URL almashtiradi (sinov uchun).
set -euo pipefail

REPO_URL=${QUALITY_SYSTEM_REPO_URL:-git@github.com:AlbatrosPromotions/claude-quality-system-skill.git}
SRC=${CLAUDE_CONFIG_DIR:-$HOME/.claude}
SKILL=quality-system
SUITES=(scripts/tests scripts/git-guard/tests scripts/review-gate/tests)
SKIP_TESTS=0
MSG=""
for arg in "$@"; do
  case "$arg" in
    --skip-tests) SKIP_TESTS=1 ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    --*) echo "noma'lum parametr: $arg" >&2; exit 1 ;;
    *)
      [ -z "$MSG" ] || { echo "commit xabari bitta bo'lishi kerak (bo'sh joyli xabarni qo'shtirnoqqa oling): $arg" >&2; exit 1; }
      MSG=$arg ;;
  esac
done
MSG=${MSG:-"sync from ~/.claude $(date +%F)"}

command -v rsync >/dev/null || { echo "rsync kerak" >&2; exit 1; }
[ -d "$SRC/skills/$SKILL" ] || { echo "$SRC/skills/$SKILL topilmadi" >&2; exit 1; }

# Testlar klondan OLDIN: qizil skill ni hech kim o'rnatmasligi uchun nashr qilinmaydi.
if [ "$SKIP_TESTS" = 1 ]; then
  echo "==> testlar o'tkazib yuborildi (--skip-tests)"
else
  command -v python3 >/dev/null || { echo "python3 kerak (testlar uchun; yoki --skip-tests)" >&2; exit 1; }
  TMP=${TMPDIR:-/tmp}
  LOG=$(mktemp "${TMP%/}/quality-system-publish.XXXXXX")
  for suite in "${SUITES[@]}"; do
    dir="$SRC/skills/$SKILL/$suite"
    if [ ! -d "$dir" ]; then
      echo "==> $suite yo'q, o'tkazib yuborildi"
      continue
    fi
    echo "==> test: $suite"
    if PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s "$dir" >"$LOG" 2>&1; then
      echo "    $(grep -E '^Ran ' "$LOG" | tail -n 1) OK"
    else
      tail -n 30 "$LOG" >&2
      echo "XATO: $suite testlari o'tmadi, nashr qilinmadi (to'liq log: $LOG)" >&2
      exit 1
    fi
  done
  rm -f "$LOG"
fi

CLONE=$(mktemp -d)
trap 'rm -rf "$CLONE"' EXIT
git clone --quiet --depth 1 "$REPO_URL" "$CLONE"

mkdir -p "$CLONE/skills"
# --delete-excluded: nashr qilinmaydigan fayllar (kesh, zaxira nusxa) repo'dan ham olib tashlanadi
rsync -a --delete --delete-excluded --exclude __pycache__ --exclude '*.bak.*' --exclude .DS_Store \
  "$SRC/skills/$SKILL/" "$CLONE/skills/$SKILL/"

if [ -z "$(git -C "$CLONE" status --porcelain)" ]; then
  echo "nashr qiladigan o'zgarish yo'q"
  exit 0
fi
git -C "$CLONE" add -A
git -C "$CLONE" commit --quiet -m "$MSG"
git -C "$CLONE" push --quiet origin HEAD
echo "nashr qilindi: $(git -C "$CLONE" rev-parse HEAD) -> $REPO_URL"
