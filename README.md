# quality-system — Claude Code skilli

Kodni Claude yozadigan va odam ko'rib chiqmaydigan proyektlar uchun avtonom sifat tizimi: tanqidiy audit metodikasi, "yozuvchi o'zini tasdiqlamaydi" qoidasi, mustaqil reviewer, spekdan testlar, mutatsiya, darvoza va tanqidchi kanareykalari, "topilma → qoida" halqasi, handoff gigiyenasi va `main` ni himoyalovchi o'rnatiladigan uch qatlamli git himoyasi.

sos-scrum loyihasida qurilgan va sinalgan (2026-10-24).

## O'rnatish

```bash
git clone git@github.com:AlbatrosPromotions/claude-quality-system-skill.git
mkdir -p ~/.claude/skills
cp -R claude-quality-system-skill/skills/quality-system ~/.claude/skills/
```

Shundan keyin Claude Code da "sifat tizimini o'rnat", "tanqidiy ko'rik qil" deb so'rang yoki `/quality-system` ni chaqiring.

## Git himoyasini proyektga o'rnatish

```bash
python3 ~/.claude/skills/quality-system/scripts/git-guard/install.py <proyekt-papkasi> --dry-run
python3 ~/.claude/skills/quality-system/scripts/git-guard/install.py <proyekt-papkasi> --merge-style merge
```

Batafsil: `skills/quality-system/SKILL.md` va `skills/quality-system/scripts/git-guard/README.md`.

## Holat

- Sinalgan: git himoyasi (uch qatlam, 76 test), lokal `make ci` izi va merge hooki naqshi, "topilma → qoida" (geometriya testlari).
- Loyihalangan, hali real proyektda qurilmagan: mustaqil reviewer va ko'rik izi, spek testlari va `spec_guard`, darvoza va tanqidchi kanareykalari, pul/vaqt uchun mustaqil hisoblagich. Qurilgach skillga qaytariladi va "proven" deb belgilanadi.
