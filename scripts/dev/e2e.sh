#!/usr/bin/env bash
# Сквозной прогон агента на учебном кейсе «Орбита Кино» в неинтерактивном режиме.
# Проверяет полный путь: запрос → постановка → сбор → исследование → варианты → стратегия → решение →
# НОВЫЙ ЧАТ → пересмотр при новых данных → НОВЫЙ ЧАТ → «на чём остановились». Ответы человека заданы заранее.
#
#   bash scripts/dev/e2e.sh [папка_логов]
#
# Важно:
# - запускает Claude Code (claude) и тратит токены вашей подписки / API;
# - папка должна быть доверенной: один раз откройте её обычным образом (claude), примите диалог доверия и MCP-серверы;
# - развилки в этом прогоне подтверждает сам сценарий (STRATEG_AUTOGATE=1). В журнале проекта и в ./strateg check
#   они помечаются как «тестовый режим» — за настоящего человека агент их не выдаёт;
# - на Windows запускайте в Git Bash.
set -u
cd "$(dirname "$0")/../.."
LOG="${1:-e2e_logs}"; mkdir -p "$LOG"
MODEL="${E2E_MODEL:-sonnet}"
export STRATEG_AUTOGATE=1
COMMON=(--model "$MODEL" --output-format json --permission-mode "${E2E_PERMISSION_MODE:-acceptEdits}" --max-budget-usd "${E2E_TURN_BUDGET:-5}")

turn() {  # turn <номер> <continue|new> <сообщение>
  local n="$1" mode="$2" msg="$3"; local extra=()
  [ "$mode" = "continue" ] && extra=(--continue)
  echo "=== ход $n ($mode) $(date +%H:%M:%S): ${msg:0:90}…"
  timeout "${E2E_TURN_TIMEOUT:-1800}" claude -p "$msg" "${COMMON[@]}" "${extra[@]}" > "$LOG/turn$n.json" 2> "$LOG/turn$n.err"
  local code=$?
  bash .claude/hooks/py.sh - "$LOG/turn$n.json" <<'PY' || true
import json, sys
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
    print(f"    итог: шагов={d.get('num_turns')} стоимость=${d.get('total_cost_usd', 0):.2f} ошибка={d.get('is_error')}")
    print("    " + (d.get("result") or "")[-700:].replace("\n", "\n    "))
except Exception as e:
    print("    не удалось разобрать ответ:", e)
PY
  ./strateg status --brief 2>/dev/null | sed -n '2,3p' | sed 's/^/    /'
  return $code
}

turn 1 new "/strateg Материалы лежат в examples/orbita-kino/inputs. Запрос руководителя — в examples/orbita-kino/request.md. Кейс учебный: работай только по материалам из папки, без поиска в интернете. Режим полный."
turn 2 continue "Подтверждаю постановку задачи как есть. Дополнительных материалов нет. Дальше работай самостоятельно до следующей развилки."
turn 3 continue "Согласен с твоей рекомендацией: прорабатывай рекомендованный вариант в стратегию. Продолжай до следующей развилки."
turn 4 continue "Стратегию утверждаю. Решение принимаем: ответственный — руководитель продукта, плановый пересмотр через 6 недель после запуска. Зафиксируй решение и собери итоговые отчёты (Markdown и HTML)."
turn 5 new "Пришло уточнение от финансов: examples/orbita-kino/update/finance_correction.md. Что это меняет в наших выводах и решении? Доведи пересмотр до конца и обнови отчёты."
turn 6 new "На чём мы остановились и что мне нужно решить сейчас? Покажи сводку ./strateg stats."
echo "=== проверки"
./strateg verify; ./strateg check; ./strateg stats; ./strateg html
