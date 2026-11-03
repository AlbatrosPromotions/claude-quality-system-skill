# git-guard: himoyalangan branchga (main) faqat PR orqali kirish

Loyihadan mustaqil, o'rnatiladigan to'plam. GitHub Free da server tomonida branch himoyasi yo'q, shuning uchun himoya uch qatlamda lokal quriladi.
Faqat Python 3.9+ standart kutubxonasi va bash 3.2+; pullik narsa yo'q. Manba: sos-scrum loyihasidagi ishlayotgan tizim (umumlashtirilgan).

## Qatlamlar

| Qatlam | Fayl (loyihada) | Nimani to'xtatadi |
|---|---|---|
| 1. Claude Code PreToolUse (Bash) | `.claude/hooks/guard-git.py` | himoyalangan branchga push (`main`, `HEAD:main`, `:main`, `+ref`, glob, argumentsiz push shu branchdan); `--force`, `-f`, `--force-with-lease`, `--mirror`, `--all`, `--no-verify` (qisqartmalari ham); himoyalangan branchda `git commit/merge/cherry-pick/revert/am` (`merge --ff-only`, `--abort` ruxsat); `git config core.hooksPath` ni `^(../)*.githooks$` dan boshqasiga o'rnatish/o'chirish, `-c core.hooksPath`, `GIT_CONFIG_*` bilan hooksPath; `quality.protected` ni o'zgartirish; `gh pr merge --admin`; `gh api` bilan `git/refs`, `merges`, `contents`, `pulls/N/merge` va GraphQL merge/ref mutatsiyalari; tekshiruv izlariga (`<prefiks>-ci-ok`, `<prefiks>-review-ok`, `ci-ok`, `review-ok`) qo'lda yozish. Buyruq shell kabi bo'laklanadi (heredoc, izoh, tirnoq ichidagi matn buyruq emas; `bash -c`, `eval`, `$()`, `env`/`sudo`/`timeout` o'ramlari ichiga kiradi; `if/then/else`, `for/while ... do`, `{ ...; }`, `!`, funksiya tanasi; argumentlarida `git`, `gh` yoki shell bo'lgan noma'lum o'ramlar: `xargs`, `find -exec`, `caffeinate`, ...; shell ga stdin orqali berilgan matn: `bash <<< '...'`, `bash <<EOF`, `cat <<EOF \| bash`, `echo '...' \| bash`; tirnoqsiz heredoc ichidagi `$()`; git ning o'zi bajaradigan buyruqlar: `submodule foreach`, `rebase --exec`, `bisect run`, push yoki `!` yashirgan `alias`). |
| 2. git `pre-push` | `.githooks/pre-push` | `git push` qanday chaqirilganidan qat'i nazar (skript ichidan ham; `git send-pack` hook ni ishga tushirmaydi, uni qatlam 1 to'sadi): himoyalangan branchga push, fast-forward bo'lmagan push, lokal repoda yo'q masofaviy commit |
| 3. Aniqlash `main_guard.py` | `.claude/quality/main_guard.py` | `origin/main` first-parent tarixida `baseline` dan keyin har commit GitHub serverida yaratilgan (committer `noreply@github.com`) va `merge_style` ga mos bo'lishi shart (quyida); `core.hooksPath` (repo va har ishga tushirilgan submodule da `<repo>/.githooks` ga nisbiy yo'l), `pre-push` bajariladi, `quality.protected` = config |

Qatlam 1 va 2 oldini oladi, 3 oldini ololmaydigan narsani ko'rinadigan qiladi (SessionStart da ogohlantirish, CI da qizil).

## O'rnatish

```
python3 scripts/git-guard/install.py <loyiha-ildizi> [--baseline SHA] [--ref origin/main] [--protected main,master] [--merge-style merge|squash|any] [--force] [--dry-run] [--skip-tests]
```

- Yozadi: `.claude/hooks/guard-git.py`, `.githooks/pre-push`, `.claude/quality/{main_guard.py,config.json,tests/test_git_guard.py}`; `.claude/settings.json` ga faqat qo'shadi (PreToolUse `Bash` va SessionStart, qayta ishga tushirsa takrorlamaydi).
- Boshqacha mavjud fayl bo'lsa farqni ko'rsatadi va `--force` siz hech narsa yozmaydi. `--dry-run` hech narsa yozmaydi.
- `baseline` standarti: `--ref` ning hozirgi SHA si, ya'ni "bundan keyin faqat PR merge". Eski tarix tekshirilmaydi. `config.json` bor bo'lsa qiymatlari saqlanadi.
- Keyin shu klonda hook larni yoqadi (`main_guard.py --setup`: `core.hooksPath`, `quality.protected`, submodule lar) va o'rnatilgan testlarni hamda `main_guard.py --verbose` ni ishga tushiradi.
- Commit qilinmaydi: o'zgarishlarni feature branchda PR bilan kiriting (himoyalangan branchga to'g'ridan-to'g'ri commit bloklanadi).

Loyiha darvozasiga (make ci / CI) qo'shing:

```
python3 -m unittest discover -s .claude/quality/tests
python3 .claude/quality/main_guard.py            # hook siz muhitda (CI) --no-hooks-check
```

Har yangi klonda hook lar yoqilishi shart (git config klonga xos): `python3 .claude/quality/main_guard.py --setup --verbose` (masalan `make git-hooks`).
`CLAUDE.md` ga qoida qo'shing: himoyalangan branchga faqat PR va `gh pr merge --merge`, hook larni chetlab o'tmaslik (blok = PR ochish). Block xabari "loyiha CLAUDE.md dagi merge qoidasi"ga ishora qiladi.

## Sozlama: `.claude/quality/config.json`

```json
{"ref": "origin/main", "baseline": "<SHA>", "protected": ["main", "master"], "merge_style": "merge"}
```

- `protected`: guard-git.py shu fayldan o'qiydi (avval repo ildizidan, keyin `$CLAUDE_PROJECT_DIR` dan; o'qib bo'lmasa yoki bo'sh bo'lsa main va master). Ro'yxat standartni almashtiradi, to'ldirmaydi.
- pre-push git config dan o'qiydi: `quality.protected` (bir nechta qiymat yoki vergul bilan), yo'q bo'lsa main va master. `main_guard.py` ikkalasi mos emasligini ushlaydi, `--setup` tuzatadi.
- `baseline` majburiy; yo'q bo'lsa `main_guard.py` aniq xabar bilan qizil.
- `merge_style` (`install.py --merge-style`; kalit yo'q = `merge`): GitHub dagi ruxsat etilgan merge usuliga mos bo'lsin (quyida).

## Merge usullari (`merge_style`)

`main_guard.py` first-parent tarixidagi har commit uchun GitHub committer ini ham talab qiladi: GitHub merge va squash commit larni serverda `noreply@github.com` committer i bilan yaratadi, lokal commit da committer foydalanuvchining o'zi. Shuning uchun qo'lda yasalgan "Merge pull request #1 from x" yoki "Funksiya (#12)" mavzusi o'tmaydi. (sos-scrum haqiqiy tarixida baseline dan keyingi 54 PR merge ning hammasi shu committer da.)

| `merge_style` | o'tadi |
|---|---|
| `merge` (standart) | 2 ota-onali, mavzu `Merge pull request #N from ...` ("Create a merge commit") |
| `squash` | 1 ota-onali, mavzu `(#N)` bilan tugaydi ("Squash and merge") |
| `any` | ikkalasidan biri |

Rebase merge hech birida o'tmaydi. `--merge-style` optsiyasi config dan ustun. GitHub Enterprise da committer boshqa bo'lsa, `GITHUB_COMMITTER` ni `main_guard.py` da o'zgartiring.

## Testlar

`python3 -m unittest discover -s tests -v` (to'plam papkasida; `test_install.py` o'rnatishni vaqtinchalik repolarda sinaydi) yoki o'rnatilgan loyihada `python3 -m unittest discover -s .claude/quality/tests`. Testlar real git repolarda (bare origin, submodule lar) subprocess orqali yuradi, haqiqiy konfiguratsiyaga tegmaydi. Hook jadvaliga yangi bloklanadigan/ruxsat etiladigan buyruq qo'shsangiz, `CASES` ga qator qo'shing.

## Qoldiq bo'shliqlar (ataylab chetlab o'tish mumkin)

Maqsad tasodifiy xatoni to'xtatish va har chetlashni ko'rinadigan qilish, qasddan hujumga qarshi to'siq emas.

- Hamma qatlamni ataylab chetlab o'tish mumkin (hooksPath qo'lda, boshqa klon, `--no-verify` terminaldan, Claude tashqarisidagi vosita).
- Qatlam 1 faqat Bash buyruqlarini ko'radi: skript ichidagi git chaqiruvi (`python x.py`, `make deploy`), Edit/Write vositasi yoki `rm`, `chmod`, `sed -i` bilan hook, `settings.json`, `config.json` yoki `pre-push` ni o'zgartirish ko'rinmaydi (bu o'zgarish PR diff ida ko'rinadi).
- Qatlam 1 bajarilish paytida ma'lum bo'ladigan qiymatlarni ham ko'rmaydi: o'zgaruvchi (`git push $F origin main`), `$(...)` natijasi, `xargs` ning stdin i, fayldagi skript (`cat x.sh \| bash`), repoda avvaldan turgan yoki `.git/config` ga to'g'ridan-to'g'ri yozilgan git alias, `sed`/`awk` kabi filtrlardan o'tgan matn, `printf` formatlash va brace expansion (`git push {--no-verify,} ...`), bir nechta buyruqning umumiy chiqishi (`{ echo a; echo b; } \| bash`, `for ... done \| bash`), `bash <(...)`, git ning buyruq bajaradigan boshqa joylari (`core.pager`, `core.sshCommand`, `difftool -x`, `filter-branch`). Bunday joydagi yoki skript ichidagi `--no-verify` qatlam 2 ni ham o'chiradi, push ni faqat qatlam 3 u yetib borgandan keyin ko'radi.
- Noma'lum o'ram tekshiruvi ataylab keng: argumenti `git`, `gh` yoki shell bilan boshlanadigan yoki ichida `&& git ...`, `; gh ...` bo'lagi bor matn buyruq deb o'qiladi (`sed`, `awk`, `perl`, `tee` va `grep` kabi qidiruv vositalaridan keyingi argumentlar bundan mustasno; ularning ichidagi matnni qatlam 1 ko'rmaydi), shuning uchun `ssh host git push origin main`, `tool --note 'git push origin main'` va himoyalangan branchda `docker run img git commit` ham bloklanadi.
- Qatlam 2 faqat `core.hooksPath` yoqilgan klonda ishlaydi; yangi klonda `--setup` kerak (qatlam 3 SessionStart/CI da eslatadi).
- GitHub veb-interfeysi yoki administrator merge i server tomonida to'xtamaydi; bunday commit keyin qatlam 3 da ko'rinadi (GitHub committer i yo'q, ota-ona soni yoki mavzu `merge_style` ga mos emas).
- `squash`/`any` da GitHub veb-interfeysida main ni to'g'ridan-to'g'ri tahrirlash ham GitHub committer i bilan keladi; mavzuga `(#N)` yozilsa o'tib ketadi (PR raqamini GitHub API bilan tekshirish yo'q).
- Qatlam 3 oldinga qarab ko'radi: `baseline` dan eski tarix, shallow clone (baseline yo'q) va `--fetch` siz eskirgan `origin/main` tekshirilmaydi. merge usuli `merge_style` ga mos kelmasa qizil beradi (rebase merge hech qachon o'tmaydi).
- To'plam CI yashilligini tekshirmaydi: merge oldidan `make ci` izi kabi tekshiruv loyihaning o'z mexanizmi (bu to'plam faqat yo'lni yopadi: PR siz kirish yo'q).
