@AGENTS.md

## Особенности Claude Code
- Навыки (skills) лежат в `.claude/skills/`, вызываются как `/strateg`, `/frame`, `/research` и т.д.
- Субагенты — в `.claude/agents/`, вызывай их через инструмент Agent/Task по имени.
- Хуки в `.claude/settings.json`: восстановление состояния при старте, журнал действий, защита правил и секретов, учёт стоимости.
