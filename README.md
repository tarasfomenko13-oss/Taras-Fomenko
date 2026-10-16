# Taras-Fomenko

Сховище моїх діалогів Claude Code. Усі діалоги лежать у папці [`dialogs/`](dialogs/README.md),
по одному Markdown-файлу на сесію, згруповані за проєктами.

## Як перенести діалоги з ноутбука сюди

Claude Code тримає історію сесій лише локально, у `~/.claude/projects/`
(на Windows: `C:\Users\<ім'я>\.claude\projects\`). Хмарна сесія до ноутбука не дістається,
тому експорт запускається на ноутбуці один раз (і повторно, коли з'являться нові діалоги).

Потрібні `git` і `python3` (на Windows команда `python`).

### macOS / Linux

```bash
git clone https://github.com/tarasfomenko13-oss/Taras-Fomenko.git
cd Taras-Fomenko
python3 tools/export_claude_dialogs.py
git add dialogs && git commit -m "Add dialogs from laptop" && git push
```

### Windows (PowerShell)

```powershell
git clone https://github.com/tarasfomenko13-oss/Taras-Fomenko.git
cd Taras-Fomenko
python tools\export_claude_dialogs.py
git add dialogs; git commit -m "Add dialogs from laptop"; git push
```

Якщо репозиторій уже склоновано, замість `git clone` зроби `git pull`, далі ті самі команди.

### Опції скрипта

| Опція | Що робить |
|---|---|
| `--raw` | додатково копіює сирі `.jsonl` у `dialogs/_raw/` (повна копія, але великі файли) |
| `--thinking` | включає блоки роздумів Claude |
| `--sidechain` | включає повідомлення субагентів |
| `--source <папка>` | інша папка `projects`, можна вказати кілька разів |
| `--min-messages N` | пропускати сесії, де менше N повідомлень від користувача |

Результати інструментів у Markdown обрізаються до 1500 символів, довгі аргументи до 600.
Очевидні секрети (API-ключі, токени, паролі у вигляді `key=...`) замінюються на `<REDACTED>`.

## Важливо про приватність

Цей репозиторій **публічний**. Діалоги часто містять шляхи, назви проєктів, виводи команд
і фрагменти даних. Перш ніж пушити `dialogs/`, зроби репозиторій приватним:
GitHub → Settings → General → Danger Zone → Change repository visibility → Private.
