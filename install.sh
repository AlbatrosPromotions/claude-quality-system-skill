#!/usr/bin/env bash
# install.sh — `quality-system` skill'ini shu repo'dan Claude Code config katalogiga (~/.claude) o'rnatadi.
#
#   git clone git@github.com:AlbatrosPromotions/claude-quality-system-skill.git ~/claude-quality-system-skill
#   bash ~/claude-quality-system-skill/install.sh [--hooks] [--skip-tests]
#
# --hooks      : references/settings-hooks.json dagi SessionStart hook'ini ~/.claude/settings.json ga qo'shadi
#                (repo'da yangi versiya chiqsa, Claude sessiya boshida yangilashni taklif qiladi).
# --skip-tests : skript testlarini ishga tushirmaydi (aks holda uch to'plam, log: /tmp/quality-system-tests.log).
# Yangilash:  git -C ~/claude-quality-system-skill pull && bash ~/claude-quality-system-skill/install.sh [--hooks]
# Yangilanish taklifi: --hooks bilan o'rnatilgan mashinada SessionStart hook kuniga bir marta (fonda, git ls-remote)
#           repo HEAD ni o'rnatilgan commit bilan solishtiradi; yangisi bo'lsa Claude sessiya boshida yangilashni taklif qiladi.
#           O'chirish: QUALITY_SYSTEM_UPDATE_CHECK=0 yoki ~/.claude/quality-system-install.json ni o'chirish.
# Claude boshqa katalogdan config o'qisa:  CLAUDE_CONFIG_DIR=/path bash install.sh
# git-guard va review-gate to'plamlari bu yerda o'rnatilmaydi: har loyihaga skill ichidagi scripts/*/install.py bilan.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
SKILL=skills/quality-system
SKILL_DEST="$CLAUDE_DIR/$SKILL"
TEST_LOG="${QUALITY_SYSTEM_TEST_LOG:-/tmp/quality-system-tests.log}"
APPLY_HOOKS=0
SKIP_TESTS=0
FLAGS=""
for arg in "$@"; do
  case "$arg" in
    --hooks) APPLY_HOOKS=1 ;;
    --skip-tests) SKIP_TESTS=1 ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "noma'lum parametr: $arg" >&2; exit 1 ;;
  esac
done
# Yangilanish taklifida faqat shu flag takrorlanadi (--skip-tests har safar yangidan tanlanadi).
if [ "$APPLY_HOOKS" = 1 ]; then FLAGS="--hooks"; fi

for p in "$SKILL" CLAUDE.md.section; do
  [ -e "$REPO_DIR/$p" ] || { echo "XATO: $REPO_DIR/$p topilmadi (repo to'liq clone qilinganmi?)" >&2; exit 1; }
done

echo "==> Claude config katalogi: $CLAUDE_DIR"
mkdir -p "$CLAUDE_DIR/skills"

# Skill: eski nusxa bo'lsa zaxiralab, yangisini ko'chiramiz. Zaxira skills/ dan tashqarida turadi: u yerdagi SKILL.md li
# papka Claude Code da ikkinchi skill bo'lib yuklanardi (har yangilanishda bittadan).
if [ -d "$SKILL_DEST" ] && diff -rq -x __pycache__ -x .DS_Store "$REPO_DIR/$SKILL" "$SKILL_DEST" >/dev/null 2>&1; then
  echo "==> $SKILL o'zgarmagan, o'tkazib yuborildi"
else
  if [ -d "$SKILL_DEST" ]; then
    bak="$CLAUDE_DIR/quality-system.bak.$(date +%Y%m%d%H%M%S)"
    [ ! -e "$bak" ] || bak="$bak.$$"
    echo "==> mavjud $SKILL -> $bak ga zaxiralandi"
    mv "$SKILL_DEST" "$bak"
  fi
  echo "==> $SKILL o'rnatilmoqda"
  cp -R "$REPO_DIR/$SKILL" "$SKILL_DEST"
fi

# CLAUDE.md: lead agent skill'ni qachon yuklashini bilishi uchun "Sifat tizimi" bo'limi kerak.
CM="$CLAUDE_DIR/CLAUDE.md"
if [ -f "$CM" ] && grep -q 'quality-system' "$CM"; then
  echo "==> CLAUDE.md da Sifat tizimi bo'limi allaqachon bor, o'zgartirilmadi"
else
  echo "==> CLAUDE.md ga Sifat tizimi bo'limi qo'shilmoqda"
  if [ -s "$CM" ]; then
    [ -z "$(tail -c 1 "$CM")" ] || printf '\n' >> "$CM"  # oxirgi qator yangi qator bilan tugamagan bo'lsa yopamiz
    printf '\n' >> "$CM"
  fi
  cat "$REPO_DIR/CLAUDE.md.section" >> "$CM"
fi

# Tekshiruvlar
HOOKS_FAILED=0
echo "==> tekshiruv"
if command -v python3 >/dev/null 2>&1; then
  echo "    python3: $(python3 --version 2>&1)"
  python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' || echo "    OGOHLANTIRISH: skriptlar python3 3.9+ talab qiladi"
  if python3 -m compileall -q "$SKILL_DEST"; then
    echo "    skriptlar kompilyatsiya qilindi (ok)"
  else
    echo "    OGOHLANTIRISH: ba'zi skriptlar kompilyatsiya qilinmadi (yuqoridagi xatoga qarang)"
  fi
  if [ "$SKIP_TESTS" = 1 ]; then
    echo "    testlar o'tkazib yuborildi (--skip-tests)"
  elif command -v git >/dev/null 2>&1; then  # testlar git repo'larini quradi
    : > "$TEST_LOG"
    for suite in scripts/tests scripts/git-guard/tests scripts/review-gate/tests; do
      if [ ! -d "$SKILL_DEST/$suite" ]; then
        echo "    testlar: $suite yo'q, o'tkazib yuborildi"
        continue
      fi
      echo "==> $suite" >> "$TEST_LOG"
      if PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s "$SKILL_DEST/$suite" >> "$TEST_LOG" 2>&1; then
        echo "    testlar: $suite: $(grep -E '^Ran ' "$TEST_LOG" | tail -n 1) OK"
      else
        echo "    OGOHLANTIRISH: $suite testlari o'tmadi, qarang: $TEST_LOG"
      fi
    done
  else
    echo "    OGOHLANTIRISH: git topilmadi, testlar o'tkazib yuborildi"
  fi
  find "$SKILL_DEST" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
  if [ "$APPLY_HOOKS" = 1 ]; then
    echo "==> yangilanish hook'i qo'shilmoqda"
    if ! python3 "$SKILL_DEST/scripts/apply_hooks.py" --settings "$CLAUDE_DIR/settings.json"; then
      echo "XATO: hook qo'shilmadi (yuqoridagi xabarga qarang); settings.json ni to'g'rilab, install.sh ni qayta ishga tushiring" >&2
      HOOKS_FAILED=1
    fi
  else
    echo "    hook qo'shilmadi; ko'rish: python3 $SKILL_DEST/scripts/apply_hooks.py --dry-run  (yoki install.sh --hooks)"
  fi
  # Yangilanish taklifi: nima o'rnatilgani $CLAUDE_DIR/quality-system-install.json ga yoziladi (commit, remote, shu clone, flag).
  # SessionStart hook kuniga bir marta fonda (git ls-remote, o'sha deploy key) repo HEAD ni u bilan solishtiradi va yangisi
  # bo'lsa Claude sessiya boshida yangilashni taklif qiladi. O'chirish: QUALITY_SYSTEM_UPDATE_CHECK=0 yoki shu faylni o'chirish.
  echo "==> o'rnatilgan versiya: $(python3 "$SKILL_DEST/scripts/update_check.py" --installed "$REPO_DIR" --flags "$FLAGS" 2>&1 || true)"
else
  echo "    OGOHLANTIRISH: python3 topilmadi: skill matni ishlaydi, lekin scripts/*.py (update_check, apply_hooks, git-guard, review-gate) ishlamaydi"
  if [ "$APPLY_HOOKS" = 1 ]; then
    echo "XATO: --hooks uchun python3 kerak" >&2
    HOOKS_FAILED=1
  fi
fi
if command -v claude >/dev/null 2>&1; then
  echo "    claude: $(claude --version 2>&1 | head -1)"
else
  echo "    OGOHLANTIRISH: 'claude' PATH da topilmadi (Claude Code o'rnatilganmi?)"
fi

echo "==> o'rnatilgan fayllar:"
(cd "$CLAUDE_DIR" && find "$SKILL" -type f -not -path '*/tests/*' | sort | sed 's|^|    |')
echo
if [ "$HOOKS_FAILED" = 1 ]; then
  echo "Skill o'rnatildi, lekin hook qo'shilmadi (yuqoridagi XATO ga qarang)." >&2
  exit 1
fi
echo "Tayyor. Tekshirish:  claude  ->  /quality-system  (skill yuklanishi kerak)"
echo "Loyihaga o'rnatish:  python3 $SKILL_DEST/scripts/git-guard/install.py <loyiha-ildizi> --dry-run  (keyin scripts/review-gate/install.py)"
