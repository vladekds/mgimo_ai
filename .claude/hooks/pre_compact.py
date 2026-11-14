#!/usr/bin/env python3
"""PreCompact: перед сжатием контекста сохраняем контрольную точку в журнал.
После сжатия хук SessionStart снова покажет агенту состояние из файлов — память не зависит от окна контекста."""
import datetime as dt

from _common import active_project_dir, read_input

read_input()
pd = active_project_dir()
if pd is not None:
    try:
        with (pd / "journal.md").open("a", encoding="utf-8") as f:
            f.write(f"- `{dt.datetime.now().astimezone().isoformat(timespec='seconds')}` **[система]** "
                    "Контекст чата сжат; состояние восстановлено из файлов проекта\n")
    except Exception:
        pass
