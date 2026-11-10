#!/usr/bin/env python3
"""Профиль таблицы до анализа: python3 scripts/tools/profile_table.py <file.csv|.xlsx> [--sheet имя]
Показывает размер, типы столбцов, пропуски, уникальные значения, диапазоны чисел и дат, дубли строк.
Работает на стандартной библиотеке; для XLSX использует openpyxl, если установлен, иначе встроенный разбор."""
import argparse
import csv
import datetime as dt
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def load(path: Path, sheet=None):
    ext = path.suffix.lower()
    if ext in (".csv", ".tsv", ".txt"):
        with path.open(encoding="utf-8-sig", errors="ignore") as f:
            sample = f.read(4096); f.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
            except csv.Error:
                dialect = csv.excel
            rows = list(csv.reader(f, dialect))
        return {path.name: rows}
    if ext in (".xlsx", ".xlsm"):
        try:
            import openpyxl  # type: ignore
            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
            return {ws.title: [["" if v is None else v for v in r] for r in ws.iter_rows(values_only=True)]
                    for ws in wb.worksheets if sheet in (None, ws.title)}
        except ImportError:
            from ingest import xlsx
            out, cur = {}, None
            for line in xlsx(path, max_rows=10**6).splitlines():
                m = re.match(r"## Лист «(.*)»", line)
                if m:
                    cur = m.group(1); out[cur] = []
                elif line.startswith("|") and cur:
                    out[cur].append([c.strip() for c in line.strip("|").split("|")])
            return {k: v for k, v in out.items() if sheet in (None, k)}
    sys.exit("Поддерживаются CSV/TSV/XLSX")


def as_num(v):
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(" ", "").replace(" ", "").replace(",", ".").rstrip("%")
    try:
        return float(s)
    except ValueError:
        return None


def as_date(v):
    if isinstance(v, (dt.date, dt.datetime)):
        return v if isinstance(v, dt.date) else v.date()
    s = str(v).strip()[:10]
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%Y.%m.%d", "%m/%d/%Y"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def profile(name, rows):
    rows = [r for r in rows if any(str(c).strip() for c in r)]
    if not rows:
        print(f"## {name}: пусто"); return
    header, data = [str(h) for h in rows[0]], rows[1:]
    print(f"## {name}: {len(data)} строк × {len(header)} столбцов")
    dups = len(data) - len({tuple(map(str, r)) for r in data})
    if dups:
        print(f"⚠️ Дублей строк: {dups}")
    print("| Столбец | Тип | Пропуски | Уникальных | Мин | Макс | Пример |")
    print("|---|---|---|---|---|---|---|")
    for i, h in enumerate(header):
        col = [r[i] if i < len(r) else "" for r in data]
        filled = [c for c in col if str(c).strip() != ""]
        miss = len(col) - len(filled)
        nums = [x for x in (as_num(c) for c in filled) if x is not None]
        dates = [x for x in (as_date(c) for c in filled) if x is not None]
        if filled and len(dates) >= 0.8 * len(filled):
            t, mn, mx = "дата", min(dates), max(dates)
        elif filled and len(nums) >= 0.8 * len(filled):
            t, mn, mx = "число", f"{min(nums):g}", f"{max(nums):g}"
        else:
            t, mn, mx = "текст", "", ""
        pct = f"{miss} ({100 * miss / max(len(col), 1):.0f}%)"
        ex = str(filled[0])[:30] if filled else ""
        print(f"| {h} | {t} | {pct} | {len(set(map(str, filled)))} | {mn} | {mx} | {ex} |")
    print("\nПроверьте: единицы измерения, определение метрик, полноту последнего периода, смену методики.\n")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("file"); ap.add_argument("--sheet"); a = ap.parse_args()
    for name, rows in load(Path(a.file), a.sheet).items():
        profile(name, rows)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
