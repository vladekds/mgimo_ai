#!/usr/bin/env python3
"""SessionStart: при открытии нового чата агент сразу видит, где остановилась работа.
Это механизм «продолжения после перерыва»: состояние берётся из файлов, а не из памяти чата."""
import subprocess
import sys

from _common import ROOT, emit, read_input, active_project_dir

data = read_input()
pd = active_project_dir()
if pd is None:
    ctx = ("[Стратег] Активного проекта нет. Если пользователь ставит продуктовую задачу — "
           "начни со skill `strateg` (создаст проект и начнёт постановку задачи).")
else:
    try:
        out = subprocess.run([sys.executable, str(ROOT / "scripts" / "strateg.py"), "status"],
                             capture_output=True, text=True, encoding="utf-8", timeout=20).stdout
    except Exception as e:  # noqa
        out = f"(не удалось прочитать статус: {e})"
    ctx = ("[Стратег] Обнаружен активный проект. Сохранённое состояние ниже — продолжай с «Следующего шага», "
           "не переделывая завершённые этапы. Перед продолжением кратко напомни пользователю, где остановились.\n\n" + out)
emit({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": ctx}})
