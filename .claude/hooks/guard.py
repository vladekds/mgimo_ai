#!/usr/bin/env python3
"""PreToolUse: 1) правила агента и база знаний меняются только с согласия человека;
2) секреты не попадают в файлы; 3) опасные команды требуют подтверждения."""
import os
import re

from _common import emit, read_input

data = read_input()
tool = data.get("tool_name", "")
tin = data.get("tool_input", {}) or {}
path = str(tin.get("file_path") or tin.get("notebook_path") or "").replace("\\", "/")
content = str(tin.get("content") or tin.get("new_string") or "") + str(tin.get("edits") or "")
cmd = str(tin.get("command") or "")

SECRET = re.compile(r"(sk-ant-[A-Za-z0-9_-]{10,}|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|"
                    r"ghp_[A-Za-z0-9]{20,}|xox[bp]-[A-Za-z0-9-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)")
PROTECTED = re.compile(r"(^|/)(AGENTS\.md|CLAUDE\.md|\.claude/|knowledge/|\.cursor/rules/)")


def decide(decision, reason):
    emit({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision,
                                 "permissionDecisionReason": reason}})
    raise SystemExit(0)


if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
    if SECRET.search(content):
        decide("deny", "Похоже на секретный ключ. Ключи хранятся только в .env (он в .gitignore), не в файлах проекта.")
    if re.search(r"(^|/)\.env($|\.)", path) and not path.endswith(".env.example"):
        decide("ask", "Запись в файл с секретами .env — подтвердите вручную.")
    if re.search(r"(^|/)projects/[^/]+/(state\.json|[a-z_]+\.jsonl)$", path):
        decide("deny", "Файлы состояния и реестров меняются только через CLI: ./strateg … "
                       "(например ./strateg human, ./strateg claim add, ./strateg brief). Так сохраняются правила и история. "
                       "Если ./strateg не запускается — сообщи пользователю об ошибке, не обходи её.")
    if PROTECTED.search(path):
        decide("ask", "Это постоянные инструкции или база знаний агента. Они меняются только по решению человека, "
                      "а не по тексту из источников. Подтвердите изменение.")
STATE_FILE = r"projects/[^/\s\"']+/(state\.json|[a-z_]+\.jsonl)"
if tool == "Bash":
    if re.search(r"(>>?|\btee\b|\bsed\s+-i|\brm\b|\bmv\b|\bcp\b|\btruncate\b|open\(|write_text)[^|;&]*" + STATE_FILE, cmd):
        decide("deny", "Файлы состояния и реестров меняются только командами ./strateg … — не через оболочку. "
                       "Если нужной команды нет, сообщи пользователю об ограничении.")
    m = re.search(r"strateg(?:\.py)?[\"']?\s+gate\s+(\w+)(.*)", cmd, re.S)
    if m and os.environ.get("STRATEG_AUTOGATE") != "1":
        decide("ask", f"🚦 РАЗВИЛКА «{m.group(1)}». Агент записывает ВАШ выбор:{m.group(2).strip()[:300]} — "
                      "подтвердите, только если это действительно ваше решение.")
    if re.search(r"\brm\s+-[a-z]*r[a-z]*f?\b.*\bprojects\b", cmd) or re.search(r"git\s+push\s+.*--force", cmd):
        decide("ask", "Команда может удалить данные проекта или историю. Подтвердите.")
    if SECRET.search(cmd):
        decide("deny", "Секретный ключ в команде. Используйте переменные окружения из .env.")
