#!/usr/bin/env bash
# Находит рабочий Python 3.9+ на Windows (Git Bash), macOS и Linux и запускает скрипт.
# На Windows "python3" часто ведёт на заглушку Microsoft Store — поэтому проверяем, а не верим имени.
CACHE="$(dirname "$0")/.python"
if [ -f "$CACHE" ]; then
  PY="$(cat "$CACHE")"
  "$PY" -c "" >/dev/null 2>&1 && exec "$PY" "$@"
fi
for c in python3 python py; do
  if "$c" -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >/dev/null 2>&1 </dev/null; then
    echo "$c" > "$CACHE" 2>/dev/null
    exec "$c" "$@"
  fi
done
echo "Стратег: не найден Python 3.9+. Установите с python.org (галочка «Add to PATH»)." >&2
exit 0
