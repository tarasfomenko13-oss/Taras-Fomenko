#!/usr/bin/env python3
"""
Експорт діалогів Claude Code з ноутбука у Markdown.

Claude Code зберігає кожну сесію локально у файлі
  ~/.claude/projects/<проєкт>/<session-id>.jsonl
Цей скрипт читає всі такі файли та записує їх у папку `dialogs/` цього
репозиторію: один читабельний .md на кожну сесію плюс індекс dialogs/README.md.

Запуск (з кореня репозиторію):
    python3 tools/export_claude_dialogs.py            # усі проєкти
    python3 tools/export_claude_dialogs.py --raw      # також копіювати сирі .jsonl
    python3 tools/export_claude_dialogs.py --source "D:/інша/папка/projects"

Потрібен лише Python 3.8+ без сторонніх бібліотек. Працює на macOS, Windows, Linux.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

MAX_TOOL_INPUT = 600      # символів з аргументів інструмента
MAX_TOOL_RESULT = 1500    # символів з результату інструмента
MAX_TITLE = 60

# Прості маски для секретів, щоб вони випадково не потрапили в git.
SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"xox[abprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{20,}"),
    re.compile(r"(?i)((?:api[_-]?key|token|secret|password)\s*[=:]\s*[\"']?)[^\s\"']{8,}"),
]


def redact(text: str) -> str:
    for pat in SECRET_PATTERNS:
        if pat.groups:
            text = pat.sub(lambda m: m.group(1) + "<REDACTED>", text)
        else:
            text = pat.sub("<REDACTED>", text)
    return text


def shorten(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + f"\n… (обрізано, ще {len(text) - limit} символів)"


def slugify(text: str, limit: int = 40) -> str:
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE).strip().lower()
    text = re.sub(r"[\s_-]+", "-", text)
    return text[:limit].strip("-") or "dialog"


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def fmt_ts(dt: datetime | None) -> str:
    if not dt:
        return ""
    return dt.astimezone().strftime("%Y-%m-%d %H:%M")


def block_text(content) -> str:
    """tool_result.content може бути рядком або списком блоків."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict):
                if b.get("type") == "text":
                    parts.append(b.get("text", ""))
                elif b.get("type") == "image":
                    parts.append("[зображення]")
            elif isinstance(b, str):
                parts.append(b)
        return "\n".join(parts)
    return ""


def is_noise_user_text(text: str) -> bool:
    t = text.lstrip()
    return t.startswith("<command-name>") or t.startswith("<local-command-stdout>") \
        or t.startswith("<local-command-caveat>") or t.startswith("<system-reminder>")


class Session:
    def __init__(self, path: Path):
        self.path = path
        self.session_id = path.stem
        self.title: str | None = None
        self.cwd: str | None = None
        self.branch: str | None = None
        self.version: str | None = None
        self.first_ts: datetime | None = None
        self.last_ts: datetime | None = None
        self.turns: list[tuple[str, list[str]]] = []  # (роль, список рядків markdown)
        self.user_count = 0
        self.assistant_count = 0
        self.tool_names: dict[str, int] = {}
        self._first_user_text: str | None = None

    def add(self, role: str, lines: list[str]):
        if not lines:
            return
        if self.turns and self.turns[-1][0] == role:
            self.turns[-1][1].extend(lines)
        else:
            self.turns.append((role, lines))

    def load(self, include_thinking: bool, include_sidechain: bool) -> bool:
        with self.path.open("r", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                self._consume(rec, include_thinking, include_sidechain)
        return self.user_count > 0

    def _consume(self, rec: dict, include_thinking: bool, include_sidechain: bool):
        rtype = rec.get("type")
        if rtype == "summary" and rec.get("summary"):
            self.title = self.title or rec["summary"]
            return
        if rtype == "ai-title" and rec.get("aiTitle"):
            self.title = rec["aiTitle"]
            return
        if rtype not in ("user", "assistant"):
            return
        if rec.get("isSidechain") and not include_sidechain:
            return
        if rec.get("isMeta"):
            return

        ts = parse_ts(rec.get("timestamp"))
        if ts:
            self.first_ts = self.first_ts or ts
            self.last_ts = ts
        self.cwd = self.cwd or rec.get("cwd")
        self.branch = self.branch or rec.get("gitBranch")
        self.version = self.version or rec.get("version")

        msg = rec.get("message") or {}
        content = msg.get("content")
        lines: list[str] = []

        if rtype == "user":
            if isinstance(content, str):
                if content.strip() and not is_noise_user_text(content):
                    lines.append(content.strip())
                    self.user_count += 1
                    self._first_user_text = self._first_user_text or content.strip()
            elif isinstance(content, list):
                for b in content:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "text":
                        t = b.get("text", "")
                        if t.strip() and not is_noise_user_text(t):
                            lines.append(t.strip())
                            self.user_count += 1
                            self._first_user_text = self._first_user_text or t.strip()
                    elif b.get("type") == "tool_result":
                        body = shorten(block_text(b.get("content")), MAX_TOOL_RESULT)
                        flag = " (помилка)" if b.get("is_error") else ""
                        if body:
                            self.add("assistant", [f"<details><summary>Результат інструмента{flag}</summary>\n\n```\n{body}\n```\n\n</details>"])
                    elif b.get("type") == "image":
                        lines.append("[зображення від користувача]")
            self.add("user", lines)

        else:  # assistant
            if isinstance(content, str):
                content = [{"type": "text", "text": content}]
            for b in content or []:
                if not isinstance(b, dict):
                    continue
                bt = b.get("type")
                if bt == "text" and b.get("text", "").strip():
                    lines.append(b["text"].strip())
                    self.assistant_count += 1
                elif bt == "thinking" and include_thinking and b.get("thinking", "").strip():
                    lines.append(f"<details><summary>Роздуми</summary>\n\n{b['thinking'].strip()}\n\n</details>")
                elif bt == "tool_use":
                    name = b.get("name", "tool")
                    self.tool_names[name] = self.tool_names.get(name, 0) + 1
                    inp = b.get("input")
                    if isinstance(inp, dict) and name == "Bash" and "command" in inp:
                        shown = str(inp["command"])
                    else:
                        shown = json.dumps(inp, ensure_ascii=False, indent=1) if inp is not None else ""
                    shown = shorten(shown, MAX_TOOL_INPUT)
                    lines.append(f"🔧 **{name}**\n```\n{shown}\n```")
            self.add("assistant", lines)

    # ---- вивід -----------------------------------------------------------

    def display_title(self) -> str:
        t = self.title or self._first_user_text or "Без назви"
        t = " ".join(t.split())
        return t if len(t) <= MAX_TITLE else t[:MAX_TITLE].rstrip() + "…"

    def project_name(self) -> str:
        if self.cwd:
            return Path(self.cwd).name or self.cwd
        return self.path.parent.name.strip("-").split("-")[-1] or "project"

    def file_name(self) -> str:
        date = self.first_ts.astimezone().strftime("%Y-%m-%d") if self.first_ts else "0000-00-00"
        return f"{date}_{slugify(self.display_title())}_{self.session_id[:8]}.md"

    def to_markdown(self) -> str:
        out = [f"# {self.display_title()}", ""]
        meta = [
            f"- **Проєкт:** `{self.cwd or self.path.parent.name}`",
            f"- **Початок:** {fmt_ts(self.first_ts)}",
            f"- **Кінець:** {fmt_ts(self.last_ts)}",
            f"- **Повідомлень:** {self.user_count} від користувача, {self.assistant_count} від Claude",
            f"- **Сесія:** `{self.session_id}`",
        ]
        if self.branch:
            meta.append(f"- **Git-гілка:** `{self.branch}`")
        if self.tool_names:
            tools = ", ".join(f"{k} ×{v}" for k, v in sorted(self.tool_names.items(), key=lambda kv: -kv[1]))
            meta.append(f"- **Інструменти:** {tools}")
        out += meta + ["", "---", ""]
        for role, lines in self.turns:
            label = "👤 Користувач" if role == "user" else "🤖 Claude"
            out.append(f"## {label}")
            out.append("")
            out.append("\n\n".join(lines))
            out.append("")
        return redact("\n".join(out)) + "\n"


def find_session_files(sources: list[Path]) -> list[Path]:
    files: list[Path] = []
    for src in sources:
        if not src.exists():
            print(f"[пропуск] немає папки: {src}", file=sys.stderr)
            continue
        for proj in sorted(p for p in src.iterdir() if p.is_dir()):
            # лише файли сесій верхнього рівня; підпапки <session>/subagents не беремо
            files.extend(sorted(proj.glob("*.jsonl")))
    return files


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", action="append", type=Path,
                    help="папка projects Claude Code (за замовчуванням ~/.claude/projects); можна кілька разів")
    ap.add_argument("--out", type=Path, default=None,
                    help="куди писати (за замовчуванням <репозиторій>/dialogs)")
    ap.add_argument("--raw", action="store_true", help="також копіювати сирі .jsonl у dialogs/_raw/")
    ap.add_argument("--thinking", action="store_true", help="включати блоки роздумів Claude")
    ap.add_argument("--sidechain", action="store_true", help="включати повідомлення субагентів")
    ap.add_argument("--min-messages", type=int, default=1,
                    help="пропускати сесії з меншою кількістю повідомлень користувача (типово 1)")
    args = ap.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    out_dir = (args.out or repo_root / "dialogs").resolve()
    sources = args.source or [Path.home() / ".claude" / "projects"]

    files = find_session_files(sources)
    if not files:
        print("Сесій не знайдено. Перевір шлях до ~/.claude/projects або вкажи --source.", file=sys.stderr)
        return 1

    out_dir.mkdir(parents=True, exist_ok=True)
    index: list[tuple[datetime, str, str, str, int]] = []
    written = skipped = 0

    for f in files:
        s = Session(f)
        try:
            ok = s.load(args.thinking, args.sidechain)
        except OSError as e:
            print(f"[помилка] {f}: {e}", file=sys.stderr)
            continue
        if not ok or s.user_count < args.min_messages:
            skipped += 1
            continue
        proj_dir = out_dir / slugify(s.project_name(), 50)
        proj_dir.mkdir(parents=True, exist_ok=True)
        target = proj_dir / s.file_name()
        target.write_text(s.to_markdown(), encoding="utf-8")
        if args.raw:
            raw_dir = out_dir / "_raw" / proj_dir.name
            raw_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, raw_dir / f.name)
        rel = target.relative_to(out_dir).as_posix()
        index.append((s.first_ts or datetime.fromtimestamp(0, tz=timezone.utc),
                      s.project_name(), s.display_title(), rel, s.user_count))
        written += 1

    index.sort(key=lambda r: r[0], reverse=True)
    lines = ["# Діалоги Claude Code", "",
             f"Експортовано {datetime.now().astimezone().strftime('%Y-%m-%d %H:%M')} · "
             f"сесій: {written} · пропущено порожніх: {skipped}", "",
             "| Дата | Проєкт | Назва | Повід. |", "|---|---|---|---|"]
    for ts, proj, title, rel, n in index:
        safe_title = title.replace("|", "\\|")
        lines.append(f"| {fmt_ts(ts)} | {proj} | [{safe_title}]({rel}) | {n} |")
    (out_dir / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"Готово: {written} діалогів записано у {out_dir} (пропущено порожніх: {skipped}).")
    print("Далі: git add dialogs && git commit -m \"Add dialogs\" && git push")
    return 0


if __name__ == "__main__":
    sys.exit(main())
