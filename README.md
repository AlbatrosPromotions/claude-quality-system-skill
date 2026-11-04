# claude-quality-system-skill

Claude Code uchun `quality-system` skill'i: kodni Claude yozadigan va odam ko'rib chiqmaydigan loyihalar uchun avtonom sifat tizimi (tanqidiy audit, mustaqil reviewer, spekdan testlar, "topilma → qoida" halqasi) va har loyihaga o'rnatiladigan ikki to'plam: `git-guard` (`main` ga faqat PR orqali kirish) va `review-gate` (ko'rik va darvoza izlari, merge hooki, topilmalar jurnali, spek himoyasi). Stack'ga bog'liq emas: faqat Python 3.9+ standart kutubxonasi va bash, pullik narsa yo'q.

Manba (source of truth): lokal `~/.claude` (private repo `AlbatrosPromotions/claude-config`). Bu repo undan `scripts/publish.sh` bilan ko'chirilgan nusxa, serverlarga o'rnatish uchun.

## Tarkib

```
skills/quality-system/
  SKILL.md, CHANGELOG.md   qoidalar (lead kontekstiga yuklanadi), o'zgarishlar tarixi
  references/              audit, komponentlar, handoff, reviewer va auditor shablonlari, settings-hooks.json
  scripts/git-guard/       main ga faqat PR orqali: 3 qatlam, install.py, tests/
  scripts/review-gate/     ko'rik va darvoza izlari, hook'lar, jurnal, spek himoyasi: install.py, tests/
  scripts/                 update_check.py, apply_hooks.py, publish.sh (Mac -> shu repo), tests/
CLAUDE.md.section          install.sh global CLAUDE.md ga qo'shadigan "Sifat tizimi" bo'limi
install.sh                 serverda: ~/.claude ga o'rnatadi (idempotent, eski nusxani zaxiralaydi)
tests/                     install.sh testi
```

## O'rnatish

Talablar: Claude Code (`claude`), `git`, `python3` (3.9+). Repo hozir public: clone uchun kalit shart emas (`https://github.com/AlbatrosPromotions/claude-quality-system-skill.git` ham bo'ladi). Private qilinsa server GitHub'ga kira olishi kerak (quyida).

```bash
git clone git@github.com:AlbatrosPromotions/claude-quality-system-skill.git ~/claude-quality-system-skill
bash ~/claude-quality-system-skill/install.sh --hooks
```

Skript nima qiladi:
- `skills/quality-system` ni `~/.claude/skills/` ga ko'chiradi (farq qilsa eski nusxani `~/.claude/quality-system.bak.<vaqt>` ga zaxiralaydi, `skills/` ichiga emas, chunki u yerdagi SKILL.md ikkinchi skill bo'lib yuklanishi mumkin), `~/.claude/CLAUDE.md` ga "Sifat tizimi" bo'limini qo'shadi (bor bo'lsa tegmaydi);
- uchala test to'plamini ishga tushiradi (3–5 daqiqa, ko'pi git-guard va review-gate to'plamlari; log `/tmp/quality-system-tests.log`; o'tkazish: `--skip-tests`);
- `--hooks` bilan SessionStart hook'ni (yangilanish taklifi, quyida) `~/.claude/settings.json` ga qo'shadi; boshqa sozlamalarga tegmaydi;
- o'rnatilganini `~/.claude/quality-system-install.json` ga yozadi (commit, remote, clone yo'li, flaglar).

Claude boshqa katalogdan config o'qisa: `CLAUDE_CONFIG_DIR=/path bash ~/claude-quality-system-skill/install.sh --hooks`. Tekshirish: `claude` ni ochib `/quality-system` yozing.

### Server GitHub'ga qanday kiradi (deploy key, read-only)

```bash
ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519 -C "server-claude-quality-system"
cat ~/.ssh/id_ed25519.pub
```

Public key'ni repo'ga qo'shing: GitHub → repo → Settings → Deploy keys → Add (read-only yetarli), yoki Mac'da:

```bash
gh repo deploy-key add /path/to/server_id_ed25519.pub --repo AlbatrosPromotions/claude-quality-system-skill --title server
```

Muqobil: serverda `gh auth login` qilib, HTTPS orqali clone qilish. Bitta deploy key faqat bitta repo'ga qo'shiladi: `~/.ssh/id_ed25519` boshqa repo (masalan delegate) uchun turgan bo'lsa, `ssh-keygen` uni ustiga yozmasin, muqobil yo'lni tanlang.

## Yangilash

```bash
git -C ~/claude-quality-system-skill pull && bash ~/claude-quality-system-skill/install.sh --hooks
```

O'rnatishdagi flaglarni (`--hooks`) yangilashda ham bering.

### Yangilanish taklifi

`--hooks` bilan o'rnatilgan mashinada SessionStart hook kuniga bir marta fonda `update_check.py --check` ni ishga tushiradi: u `git ls-remote` bilan repo HEAD ni o'rnatilgan commit bilan solishtiradi va natijani `~/.claude/quality-system-update.json` ga yozadi. Repo oldinda bo'lsa keyingi sessiya boshida Claude bir qator bilan xabar beradi va yuqoridagi buyruqni taklif qiladi; uni faqat siz rozi bo'lsangiz ishga tushiradi.

O'chirish: `QUALITY_SYSTEM_UPDATE_CHECK=0` yoki `~/.claude/quality-system-install.json` ni o'chirish. Mac'da (manba `~/.claude`) bu fayl yo'q, shuning uchun u yerda tekshiruv ham yo'q.

## Loyihaga o'rnatish

Har loyihaga ikki to'plam alohida o'rnatiladi: avval git-guard, keyin review-gate (u git-guard siz o'rnatilmaydi). `--dry-run` hech narsa yozmaydi, testlar `--skip-tests` bilan o'tkaziladi. Review-gate PR bilan `gh` (`gh auth login`) orqali ishlaydi.

**git-guard: `main` ga faqat PR orqali** (Claude Code hook'i, git `pre-push`, `main_guard.py`)

```bash
python3 ~/.claude/skills/quality-system/scripts/git-guard/install.py <loyiha> --dry-run
python3 ~/.claude/skills/quality-system/scripts/git-guard/install.py <loyiha> [--merge-style merge|squash|any]
```

Yozadi: `.claude/hooks/guard-git.py`, `.githooks/pre-push`, `.claude/quality/{main_guard.py,config.json,tests/}`; `.claude/settings.json` ga faqat qo'shadi; hook'larni yoqadi; commit qilmaydi. `--merge-style` GitHub'dagi merge usuliga mos bo'lsin (standart `merge`).

**review-gate: ko'rik va darvoza izlari**

```bash
python3 ~/.claude/skills/quality-system/scripts/review-gate/install.py <loyiha> [--prefix quality] [--reviewer opus-reviewer] [--gate-command "make ci"] [--source-labels Source,Manba]
```

Yozadi: `.claude/quality/{review_stamp.py,gate_stamp.py,quality_log.py,spec_guard.py,tests/}`; `.claude/hooks/merge-guard.py` (merge izlarsiz bloklanadi) va `review-rounds.py` (reviewer faqat byudjet ichida chaqiriladi); `.claude/agents/<reviewer>.md`; `config.json` ga yangi kalitlarni va `settings.json` ga ikki hook'ni qo'shadi. Qolgan flaglar: `--sensitive`, `--submodules`, `--spec-paths`, `--spec-extra`. Oxirida keyingi qadamlarni chiqaradi (`gate_stamp.py` ni darvoza skriptiga ulash, loyiha `CLAUDE.md` ga qoidalarni yozish).

`.claude/quality/config.json` ikkala to'plam uchun bitta; review-gate kalitlari ixtiyoriy:

| Kalit | Standart | Vazifasi |
|---|---|---|
| `ref`, `baseline`, `protected`, `merge_style` | `origin/main`, o'rnatishdagi SHA, `["main","master"]`, `merge` | git-guard |
| `prefix` | `quality` | izlar nomi: `<prefix>-ci-ok`, `<prefix>-review-ok-<PR>`, `<prefix>-review-allow` |
| `gate_command` | `make ci` | bloklash xabarlarida nomlanadi |
| `reviewer` | `opus-reviewer` | izni faqat shu agent yozadi |
| `source_labels` | `["Source"]` | PR tavsifida `<belgi>: <matn>` qatori shart |
| `sensitive` | `.claude/`, `.githooks/`, `.github/workflows/` | mos yo'l o'zgarsa hisobotda `security=done` shart |
| `submodules` | `[]` | o'z PR i bor submodule papkalari |
| `spec_paths`, `spec_prefix`, `spec_extra` | `[]`, `spec:`, `[]` | `spec_guard.py`: spek faqat `spec:` commit orqali |
| `quality_log` | `file`: `docs/quality-log.md` | `quality_log.py` sozlamalari |

Bir PR ning yo'li:

1. **Darvoza izi:** darvoza skripti boshida `python3 .claude/quality/gate_stamp.py --tree`, yashil tugasa `--write --tree "$tree"`, qizilda `--clear`. Iz = ish daraxti xeshi.
2. **Bitta ko'rik:** lead reviewer agentiga faqat PR raqami va repo papkasini beradi. Hook va agent avval `python3 .claude/quality/review_stamp.py --pr N --check` bilan tur byudjetini tekshiradi; oxirida agent `--pr N --head SHA --report FILE` bilan PR ga izoh va ko'rik izini yozadi.
3. **Bloker yoki muhim topilma:** tuzatish, darvoza qayta, bitta qayta ko'rik (2-tur). Ikkalasi ham yo'q bo'lsa 2-tur yo'q.
4. **Uchinchi ko'rik** faqat foydalanuvchi "ha" desa: `python3 .claude/quality/review_stamp.py --pr N --allow` (bir ruxsat = bir tur).
5. **Merge** faqat `gh pr merge <N> --merge` bilan; hook darvoza izi PR boshining daraxtiga, ko'rik izi shu PR ga mosligini tekshiradi.

## Mac'da nashr qilish

Skill'ni `~/.claude` da o'zgartirgach:

```bash
bash ~/.claude/skills/quality-system/scripts/publish.sh "quality-system: nima o'zgardi"
```

Skript avval uchala test to'plamini yuritadi (qizil bo'lsa nashr qilmaydi, `--skip-tests` bilan o'tkaziladi), so'ng `skills/quality-system` ni shu repo'ga ko'chirib commit va push qiladi.

## Holat

| Holat | Nima |
|---|---|
| Sinalgan | git-guard (3 qatlam), ko'rik izi va tur byudjeti, merge hooki, darvoza izi, topilmalar jurnali, spek himoyasi: hammasi 40+ PR da ishlagan loyihadan ko'chirilgan |
| Loyihalangan (real loyihada sinalmagan) | pul va vaqt uchun mustaqil hisoblagich (oracle), hajm testlari, tanqidchi kanareykasi tartibi, haftalik audit shabloni |

## Qoldiq xavf

- Hook'lar tasodifiy xato va agent og'ishidan himoya qiladi, qasddan chetlab o'tuvchidan emas.
- Yozuvchi va reviewer bir model oilasidan: ko'rik yangi kontekstda o'tadi, lekin ko'r nuqtalar umumiy.
- Noto'g'ri spek barcha tekshiruvdan o'tadi: spekning to'g'riligini faqat odam tasdiqlaydi.
- Server tomonidagi branch himoyasi pullik GitHub rejasini talab qiladi (Free da yo'q); bu yerda faqat lokal qatlamlar.

Batafsil: [`SKILL.md`](skills/quality-system/SKILL.md), [git-guard](skills/quality-system/scripts/git-guard/README.md), [review-gate](skills/quality-system/scripts/review-gate/README.md), [`CHANGELOG.md`](skills/quality-system/CHANGELOG.md).
