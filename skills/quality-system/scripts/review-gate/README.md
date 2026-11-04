# review-gate: mustaqil ko'rik darvozasi (ko'rik izi, merge qo'riqchisi, darvoza izi)

Loyihadan mustaqil, o'rnatiladigan to'plam: "yozuvchi o'zini tasdiqlamaydi" qoidasini mexanik qiladi. PR ga yangi kontekstli ko'rikchi agent ko'rik beradi; `gh pr merge` faqat darvoza izi va ko'rik izi PR ning aniq kodiga mos bo'lsa o'tadi; ko'rik turlari soni chegaralangan. Faqat Python 3.9+ standart kutubxona, git, gh. Asos: birinchi loyihadagi ishlayotgan tizim. Oldin `git-guard` o'rnatilgan bo'lishi shart: u iz fayllariga shell orqali qo'lda yozishni to'sadi; iz skriptlarining yozish shakllarini esa shu to'plamning `merge-guard.py` i to'sadi (`gate_stamp.py --write` darvoza skriptidan tashqarida, `review_stamp.py --head/--report` ko'rikchi agentdan tashqarida o'tmaydi).

## Fayllar

| Kit fayli | Loyihada | Vazifasi |
|---|---|---|
| `review_stamp.py` | `.claude/quality/` | ko'rikchi iz yozadi; tur chegarasi; `--check`; `--allow` |
| `gate_stamp.py` | `.claude/quality/` | darvoza skripti `<prefix>-ci-ok` ni yozadi, o'chiradi, tekshiradi |
| `merge_guard.py` | `.claude/hooks/merge-guard.py` | PreToolUse (Bash): `gh pr merge`; `review_stamp.py` va `gate_stamp.py` ning iz yozish shakllari |
| `review_rounds.py` | `.claude/hooks/review-rounds.py` | PreToolUse (Agent): ko'rikchi chaqiruvi tur chegarasida |
| `quality_log.py` | `.claude/quality/` | topilmalar jurnali formati, "topilma → qoida" majburiyati |
| `spec_guard.py` | `.claude/quality/` | spec testlarni faqat `spec:` commit o'zgartiradi |
| `reviewer.md` | `.claude/agents/<reviewer>.md` | agent shabloni (`{reviewer}`, `{source_label}`, `{gate_command}` to'ldiriladi) |
| `tests/test_*.py` | `.claude/quality/tests/` | to'plam testlari (`test_install.py` siz), darvozada ham yuradi |
| (o'rnatuvchi yozadi) | `.claude/quality/.gitignore` | `__pycache__/`: darvozada testlar yurganda CPython yozgan bayt-kod daraxt xeshini o'zgartirmasin (fayl bor bo'lsa foydalanuvchi qatorlari saqlanadi, yetishmasa shu qator qo'shiladi) |
| `install.py`, `README.md`, `tests/test_install.py` | o'rnatilmaydi | o'rnatuvchi, hujjat, uning testi |

## O'rnatish

```
python3 scripts/review-gate/install.py <loyiha-ildizi> [--prefix quality] [--reviewer opus-reviewer] [--gate-command "make ci"] [--source-labels Source,Ref]
    [--sensitive REGEX]... [--submodules a,b] [--spec-paths a/,b/] [--spec-extra yo'l]... [--force] [--dry-run] [--skip-tests]
```

- git-guard yo'q bo'lsa (`config.json` yoki `guard-git.py` topilmasa) kod 2 va git-guard buyrug'i ko'rsatiladi.
- Yozadi: jadvaldagi fayllar; `config.json` (mavjud kalitlar saqlanadi, yangilari CLI > mavjud > standart, faqat o'zgarsa); `.claude/settings.json` ga faqat qo'shadi: PreToolUse `Bash` (merge-guard) va `Agent` (review-rounds), takrorlamaydi.
- Boshqacha mavjud fayl yoki CLI mavjud config qiymatini o'zgartirsa farq ko'rsatiladi va `--force` siz hech narsa yozilmaydi. Istisno `.claude/quality/.gitignore`: u hech qachon ziddiyat emas, `--force` ham kerak emas (foydalanuvchi qatorlari saqlanadi; `__pycache__/` qatori yo'q bo'lsa oxiriga qo'shiladi, reja satri «qo'shiladi»; bor bo'lsa «o'zgarmagan»). `--dry-run` yozmaydi. `.gitignore` yopgan fayllarni ogohlantiradi. Commit qilmaydi.
- Keyin o'rnatilgan testlarni yuritadi (git-guard testlari bilan ~5 daqiqa; `--skip-tests`) va keyingi qadamlarni chiqaradi: `gate_stamp.py` ni darvozaga ulash, `CLAUDE.md` qoidalari, kerak bo'lsa `quality_log.py --check` va `spec_guard.py` ni darvozaga qo'shish. Yangi hook va agent yangi sessiyada yuklanadi.

## Sozlama: `.claude/quality/config.json`

git-guard bilan umumiy fayl (`ref`, `baseline`, `protected`, `merge_style` ga tegilmaydi). Hamma kalit ixtiyoriy: skriptlar yo'q yoki yaroqsiz qiymatni standartga almashtiradi (buzuq fayl: standart va bitta stderr qator), o'rnatuvchi rad etadi. Tartib: `--config` > `$QUALITY_CONFIG` > skript yonidagi `config.json` (hook lar uchun `../quality/`). Ro'yxat qiymatlari standartni almashtiradi, to'ldirmaydi.

| Kalit | Standart | Ma'nosi |
|---|---|---|
| `prefix` | `quality` | iz nomlari va PR izohi belgisi (pastda) |
| `gate_command` | `make ci` | xabarlarda ko'rinadigan darvoza buyrug'i |
| `reviewer` | `opus-reviewer` | iz yozishi mumkin bo'lgan agent; tur hooki shuni tekshiradi |
| `source_labels` | `["Source"]` | PR tavsifida `<yorliq>: <matn>` qatori shart (katta-kichik harf farqsiz, `**Yorliq:**` mumkin, `<!-- -->` hisobga olinmaydi, bo'sh matn = yo'q); ro'yxat bo'sh bo'lmaydi |
| `sensitive` | `["^\\.claude/", "^\\.githooks/", "^\\.github/workflows/"]` | o'zgargan yo'llarga `re.search`; mos kelsa hisobotda `security=done` shart |
| `submodules` | `[]` | o'z PR lari bor submodule papkalari: ko'rik o'sha papkada, o'z izlari bilan |
| `spec_paths` | `[]` | spec yo'llari (fayl yoki papka, repo ildiziga nisbiy); bo'sh = `spec_guard.py` tekshirmaydi |
| `spec_prefix`, `spec_extra` | `spec:`, `[]` | spec commit mavzusi prefiksi; `spec:` commit yana tegishi mumkin bo'lgan yo'llar |
| `quality_log` | `file: docs/quality-log.md`, `system_prefixes`, `minor_rule_from: null`, `gate_script: null` | jurnal; tizim kategoriyalari; shu sanadan tizim `minor` qatori qizil; `stage <nom>` qatorli darvoza skripti. Ixtiyoriy `check_prefixes`, `check_patterns`, `check_files`: `check_prefixes` yo'lning boshiga bog'langan, monorepo da o'zingiznikini yozing (`api/tests/`) |

## Har PR uchun oqim

1. Darvoza skripti (`gate_command`): boshida `tree=$(python3 .claude/quality/gate_stamp.py --tree)` va eski izni `--clear` bilan o'chirish; hammasi yashil bo'lsa `gate_stamp.py --write --tree "$tree"`; qizil bo'lsa `--clear`. Yurish davomida fayl o'zgarsa iz yozilmaydi. Darvozada to'plam testlarini `python3 -B -m unittest discover -s .claude/quality/tests` bilan yuriting: `-B`siz CPython testlar yoniga `__pycache__` yozadi, daraxt xeshi `--tree` bilan `--write --tree` orasida o'zgaradi va iz har safar rad etiladi (o'rnatuvchi yozgan `.claude/quality/.gitignore` ham buni yopadi). `--tree` indeks nusxasining mtime ini 1 s ga qo'yadi, shuning uchun git har chaqiruvda hamma kuzatilgan faylni qayta o'qiydi (bir soniyada hajmi teng qayta yozilgan fayl ham ko'rinadi): juda katta repo da sekinroq.
2. PR tavsifida `<yorliq>: <manba>` qatori. Lead ko'rikchini faqat PR raqami va repo papkasi bilan chaqiradi; `review-rounds.py` chegaradan tashqarisini boshlanmasdan to'xtatadi.
3. Ko'rikchi birinchi `review_stamp.py --pr N --check`, oxirida `review_stamp.py --pr N --head SHA --report FILE` ni yuritadi (hisobot oxirgi qatori `VERDICT: ...`).
4. Bloker yoki major bo'lsa: tuzatish, push, darvoza, 2-ko'rik. 1-ko'rik toza (bloker 0, major 0) bo'lsa qayta ko'rik yo'q: keyingi commitlar faqat darvoza bilan.
5. 3-ko'rik faqat foydalanuvchi "ha" degach: lead `review_stamp.py --pr N --allow [--round K]` (K >= 3, standart 3; bir ruxsat = bir tur).
6. Merge faqat loyiha papkasidan `gh pr merge <N> --merge`.

## Hook lar nimani bloklaydi (chiqish kodi 2, sabab modelga qaytadi)

- `merge-guard.py`: `gh pr merge`, agar `<prefix>-ci-ok` daraxti PR head daraxtiga teng bo'lmasa yoki `<prefix>-review-ok-<PR>` yo'q, eski shaklda, boshqa PR niki, bloker > 0, 1-turda major > 0 bo'lsa (daraxt farq qilsa, iz head i PR head ning ajdodi bo'lsa yetadi). O'qib bo'lmaydigan shakllar ham: `-R/--repo`, `GH_REPO=`, `GH_HOST=`, nishon oddiy raqam emas, `bash -c`, `eval`, `$(...)`, quvur orqali shell, juftlanmagan tirnoq. `gh` bilan `pr` yoki `merge` orasidagi bayroqlar (`gh pr --repo o/r merge 5`) merge ni yashirmaydi: u ham merge deb tanilib, `-R/--repo` bloki unga ham tegadi. Bo'sh so'z ham yashirmaydi (`gh pr '' merge 5`, `gh '' pr merge 5`: gh bo'sh argumentni o'tkazib yuboradi, shuning uchun bu oddiy merge sifatida tekshiriladi). `pr` yoki `merge` turishi kerak joydagi `$X` yoki `` `...` `` esa ish vaqtida bo'sh yoki bayroq bo'lib qolishi mumkin (`gh pr $X merge`, `X=; gh $X pr merge`): hook qiymatini bilmaydi, shuning uchun hal qilmasdan bloklaydi (nishoni noma'lum `cd` kabi); boshqa gh buyruqlaridagi `$N` (`gh pr view $N`, `--add-label merge`) to'siq emas. Ikkinchi qoida (iz skriptlarining yozish shakllari, merge dan mustaqil): `review_stamp.py` ni `--head`/`--report` bilan faqat `reviewer` agenti yuritadi (`--check`, `--allow` hammaga ochiq); `gate_stamp.py --write` (`--wr…` qisqartmalari ham) hech kimga ochiq emas, ko'rikchiga ham: darvoza izini faqat darvoza skripti yozadi (hook asbob buyrug'ini, masalan `make ci` ni, ko'radi, skript ichidagi chaqiruvni emas); `--tree`, `--check`, `--clear`, `--help` ochiq. Qoida skript DASTUR sifatida ishga tushganda ishlaydi: buyruqning o'zi yoki har qanday `python*`/`pypy*` nomining (`python3.13t`, `pythonw3`) skript argumenti, `-I -B -u` kabi optsiyalardan keyin; `bash -c`, `eval`, `$(...)` ichida ham. Ishga tushirgich orqali ham (`uv run python …`, `poetry run`, `caffeinate -i`, `xcrun`, `arch`, `docker exec`, `xargs`, `python3 -m trace`, `python3 other.py …`): buyruqning biror so'zi iz skriptining nomi, shu buyruqda yozish bayrog'i bor va dastur ma'lum «bajarmaydigan» dasturlar ro'yxatida bo'lmasa bloklanadi. Ro'yxat: `grep rg egrep fgrep ag ack cat head tail less more sed` (`-i` siz) `awk diff ls wc echo printf git stat file test [`; `python -c KOD` ham skriptni yuritmaydi (qolgan so'zlar kodga ma'lumot). Ro'yxatdagi dastur argumentidagi tilga olish oddiy matn va o'tadi. Shell skriptni stdin dan o'qisa (quvur, `<<<`, heredoc) buyruq matni ko'rinmaydi: `gh pr merge` matni kabi, xom matn bo'yicha shu ikki qoida ham qo'llanadi (ko'rik izi: reviewer ga ochiq; darvoza izi: hech kimga; `--tree`, `--check` va `cat <<EOF` kabi shell bo'lmagan dastur o'tadi).
- `review-rounds.py`: `reviewer` chaqiruvi (brief da `PR #N`, `PR N` yoki `--pr N`) `--check` dan kod 4 olsa. Boshqa agent, PR raqamsiz brief va xatolar o'tadi: chegarani skript iz yozishda baribir majburlaydi.

`review_stamp.py` chiqish kodlari: 0 iz yozildi (`--check`: tur ruxsat; `--allow`: ruxsat yozildi); 1 rad etildi, sabab stderr da (`ko'rik izi yozilmadi: ...`: PR ochiq emas, head o'zgargan, manba qatori yo'q, sezgir yo'lda `security=done` yo'q, gh xatosi); 2 noto'g'ri argument; 3 blokerlar bor (hisobot PR ga izoh, eski iz o'chadi, yangisi yo'q); 4 ko'rik turlari chegarasi (`ko'rik turlari chegarasi: ...`, hech narsa yozilmaydi).

## Iz va belgi formatlari

Iz fayllari `git rev-parse --git-path <nom>` da (worktree, submodule da o'zining):
- darvoza izi `<prefix>-ci-ok`: `<ishchi daraxt xeshi> <UTC ISO>`
- ko'rik izi `<prefix>-review-ok-<PR>`: `<daraxt> <PR> <head> <UTC ISO> round=<k> blockers=<b> major=<m> minor=<n>`
- ruxsat izi `<prefix>-review-allow`: `<PR> <tur> <UTC ISO>` (tur izohi yozilgach o'chadi)
- PR izohi belgisi: `<!-- <prefix>-review head=<sha> tree=<sha> blockers=<b> major=<m> minor=<n> security=<done|n/a> round=<k> -->`; tur raqami = belgilar soni + 1 (sessiya va mashinadan mustaqil).

`"prefix": "sos"` birinchi loyihaning nomlari va formatlarini beradi (`sos-ci-ok`, `sos-review-ok-<PR>`, `sos-review-allow`, `<!-- sos-review ... -->`).

## Testlar

`python3 -m unittest discover -s tests -v` (to'plam papkasida; har suite yuklama ostida ~1-2 daqiqa; `test_install.py` bitta testda o'rnatilgan testlarni haqiqatan yuritadi: ~5 daqiqa) yoki o'rnatilgan loyihada `python3 -B -m unittest discover -s .claude/quality/tests -p 'test_*.py'` (git-guard testlari bilan ~5 daqiqa; darvozaga qo'shing). Qora quti: subprocess, vaqtinchalik repolar, soxta `gh`; GitHub va `~/.claude` ga tegilmaydi.

## Qoldiq bo'shliqlar

Maqsad tasodifiy xato va agent og'ishini to'xtatish; faqat qasddan yoziladigan chetlab o'tish shakllari doiradan tashqari (ularni regex bilan qidirmang).

- `agent_type` Claude Code hook hodisasidan keladi (subagent ichida to'ldiriladi, asosiy sessiyada yo'q); hook unga ishonadi, tekshira olmaydi.
- Lead `review_stamp.py` ni o'z hisoboti bilan yuritsa mustaqillik yo'qoladi; skript chaqiruvchini ajrata olmaydi, uni faqat `merge-guard.py` to'xtatadi.
- Hook lar faqat Bash buyrug'ini ko'radi: skript ichidagi `gh pr merge`, Edit/Write bilan iz fayliga yozish, Claude tashqarisidagi vosita ko'rinmaydi. Server tomonida majburlash yo'q: veb-interfeysdagi merge to'xtamaydi.
- Qasddan beriladigan shakllar tanilmaydi: `echo 5 | xargs gh pr merge`, `xargs -I{} gh pr merge {}`, `find … -exec gh pr merge …`; iz skriptlari uchun ham skript nomi va yozish bayrog'i bir buyruqda turmasa (`ls … | xargs -I{} python3 {} --write`, nomni o'zgaruvchi yoki alias orqali berish). `uv run`, `poetry run`, `caffeinate`, `python3 -m trace` kabi ishga tushirgichlar orqali nom va bayroq bir buyruqda bo'lsa tanib olinadi (yuqorida). `merge` dan oldingi `$X` so'zi (`gh pr $X merge`) hal qilinmaydi: hook ish vaqtidagi qiymatini bilmaydi va bloklaydi.
- Darvoza skripti `gate_stamp.py --write` ni tekshiruvlarni yuritmasdan chaqirsa ham izni yozadi: hook skript ichini ko'rmaydi, shuning uchun darvoza skriptining to'g'riligiga ishonadi (uni ko'rik va kanareyka tekshiradi).
- Izlar lokal: boshqa klonda darvoza va ko'rik qayta kerak. Tur hisobi PR izohlarida: izoh o'chirilsa hisob qaytadi.
- Ko'rikchi sifatini to'plam isbotlamaydi: kanareyka (`references/reviewer.md`) bilan o'lchang.
