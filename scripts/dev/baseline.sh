#!/usr/bin/env bash
# «Обычный чат с ИИ» для сравнения: та же задача и те же материалы, один запрос, без инструкций, реестра,
# субагентов и хуков агента. Запускается в пустой временной папке, чтобы настройки репозитория не подхватились.
#   bash scripts/dev/baseline.sh [модель] → bench/baseline_<модель>.md и .json (стоимость, время)
set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
MODEL="${1:-sonnet}"
TMP="$(mktemp -d)"; cp -r "$ROOT/examples/orbita-kino/inputs" "$TMP/materials"; cp "$ROOT/examples/orbita-kino/request.md" "$TMP/materials/"
PROMPT='Ты помогаешь руководителю цифрового продукта. В папке materials лежат его запрос (request.md) и материалы: cohorts.csv, exit_survey.csv, support_tickets.csv, memo_june.md, interviews.md и три скриншота в materials/ux. Прочитай всё и подготовь: 1) картину ситуации; 2) варианты действий; 3) рекомендацию — продуктовую и коммуникационную стратегию и план проверки. Ответ одним документом в Markdown.'
mkdir -p "$ROOT/bench"
START=$(date +%s)
(cd "$TMP" && claude -p "$PROMPT" --model "$MODEL" --allowedTools "Read" "Glob" --output-format json) > "$ROOT/bench/baseline_$MODEL.json" 2> "$ROOT/bench/baseline_$MODEL.err"
END=$(date +%s)
bash "$ROOT/.claude/hooks/py.sh" - "$ROOT/bench/baseline_$MODEL.json" "$ROOT/bench/baseline_$MODEL.md" "$((END-START))" <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
open(sys.argv[2], "w", encoding="utf-8").write(d.get("result") or "")
meta = {"model": list((d.get("modelUsage") or {}).keys()), "cost_usd": d.get("total_cost_usd"), "seconds": int(sys.argv[3]),
        "turns": d.get("num_turns"), "chars": len(d.get("result") or "")}
json.dump(meta, open(sys.argv[1], "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(meta)
PY
rm -rf "$TMP"
