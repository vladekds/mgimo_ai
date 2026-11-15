#!/usr/bin/env python3
"""Готовые воспроизводимые расчёты для частых продуктовых вопросов (без LLM, только стандартная библиотека).

  ./strateg analyze retention <csv> --period COL --segment COL --size COL --kept COL
        удержание по периодам и сегментам + разложение изменения на «эффект состава» и «эффект внутри сегментов»
  ./strateg analyze funnel <csv> --steps COL1,COL2,COL3 [--segment COL]
        воронка: конверсия между шагами, по сегментам
  ./strateg analyze unit --price P --cost C --months N --retention "0.93,0.5,0.14" [--cac X] [--fixed-cost]
        маржа на одного привлечённого; два сценария затрат (переменные / постоянные)

Вывод — Markdown; сохраните его в work/*.out.txt и зарегистрируйте как источник kind=data.
Неполные периоды (пустые значения) исключаются из сравнений и перечисляются отдельно.
"""
import argparse
import csv
import sys
from collections import OrderedDict


def num(v):
    s = str(v).strip().replace(" ", "").replace(" ", "").replace(",", ".")
    if s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def read(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        sample = f.read(4096)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        return list(csv.DictReader(f, dialect=dialect))


def need(rows, *cols):
    miss = [c for c in cols if c and c not in rows[0]]
    if miss:
        sys.exit(f"Нет столбцов: {', '.join(miss)}. Есть: {', '.join(rows[0])}")


def pct(x):
    return "—" if x is None else f"{100 * x:.1f}%"


def retention(a):
    rows = read(a.file)
    need(rows, a.period, a.segment, a.size, a.kept)
    data, incomplete = OrderedDict(), []
    for r in rows:
        p, s, n, k = r[a.period], r[a.segment], num(r[a.size]), num(r[a.kept])
        if n is None:
            continue
        if k is None:
            incomplete.append((p, s))
            continue
        data.setdefault(p, OrderedDict())[s] = (n, k)
    partial = sorted({p for p, _ in incomplete})
    periods = [p for p in data if p not in partial]
    segs = list(OrderedDict.fromkeys(s for p in periods for s in data[p]))
    print(f"## Удержание: {a.kept} / {a.size} по «{a.period}» и «{a.segment}»\n")
    print("| Период | " + " | ".join(f"{s}: доля / удержание" for s in segs) + " | Всего |")
    print("|---|" + "---|" * (len(segs) + 1))
    table = {}
    for p in periods:
        tot_n = sum(n for n, _ in data[p].values())
        tot_k = sum(k for _, k in data[p].values())
        table[p] = {s: (data[p][s][0] / tot_n, data[p][s][1] / data[p][s][0]) for s in data[p]}
        table[p]["_all"] = tot_k / tot_n
        cells = [f"{pct(table[p][s][0])} / {pct(table[p][s][1])}" if s in table[p] else "—" for s in segs]
        print(f"| {p} | " + " | ".join(cells) + f" | **{pct(table[p]['_all'])}** |")
    if partial:
        print(f"\n⚠️ Неполные периоды исключены из сравнения (нет значения «{a.kept}»): {', '.join(partial)}.")
    if len(periods) < 2:
        return
    p0, p1 = a.base or periods[0], a.last or periods[-1]
    if p0 not in table or p1 not in table:
        sys.exit(f"Периоды для сравнения должны быть из: {', '.join(periods)}")
    print(f"\n## Разложение изменения: {p0} → {p1}\n")
    print("| Сегмент | Доля до | Доля после | Удержание до | Удержание после | Эффект состава, п.п. | Эффект внутри, п.п. |")
    print("|---|---|---|---|---|---|---|")
    mix_t = rate_t = 0.0
    notes = []
    for s in segs:
        w0, r0 = table[p0].get(s, (0.0, None))
        w1, r1 = table[p1].get(s, (0.0, None))
        if r0 is None:  # сегмента не было: его удержание «до» принимаем равным «после» (условность)
            r0 = r1
            notes.append(f"«{s}» не было в {p0}")
        if r1 is None:
            r1 = r0
            notes.append(f"«{s}» нет в {p1}")
        mix = (w1 - w0) * (r0 + r1) / 2      # симметричное разложение: сумма эффектов точно равна изменению
        rate = (r1 - r0) * (w0 + w1) / 2
        mix_t += mix
        rate_t += rate
        print(f"| {s} | {pct(w0)} | {pct(w1)} | {pct(r0)} | {pct(r1)} | {100 * mix:+.2f} | {100 * rate:+.2f} |")
    total = table[p1]["_all"] - table[p0]["_all"]
    print(f"| **Итого** | | | **{pct(table[p0]['_all'])}** | **{pct(table[p1]['_all'])}** | **{100 * mix_t:+.2f}** | **{100 * rate_t:+.2f}** |")
    share = 100 * mix_t / total if total else 0
    print(f"\nИзменение общего показателя: {100 * total:+.2f} п.п.; из них состав {100 * mix_t:+.2f} п.п. ({share:.0f}%), "
          f"внутри сегментов {100 * rate_t:+.2f} п.п.")
    if notes:
        print("Условность: " + "; ".join(notes) + " — для него удержание «до» принято равным «после».")
    print("Метод: симметричное (по средним весам) разложение на эффект состава и эффект внутри сегментов. "
          "Разложение описывает арифметику, а не причины.")


def funnel(a):
    rows = read(a.file)
    steps = [s.strip() for s in a.steps.split(",")]
    need(rows, a.segment, *steps)
    groups = OrderedDict()
    for r in rows:
        g = r[a.segment] if a.segment else "все"
        acc = groups.setdefault(g, [0.0] * len(steps))
        for i, s in enumerate(steps):
            acc[i] += num(r[s]) or 0.0
    if a.segment:
        groups["все"] = [sum(g[i] for g in list(groups.values())) for i in range(len(steps))]
    print("## Воронка\n")
    print("| Сегмент | " + " | ".join(steps) + " | " + " | ".join(f"{steps[i]}→{steps[i + 1]}" for i in range(len(steps) - 1)) + " | Сквозная |")
    print("|---|" + "---|" * (2 * len(steps)))
    for g, v in groups.items():
        conv = [pct(v[i + 1] / v[i]) if v[i] else "—" for i in range(len(steps) - 1)]
        print(f"| {g} | " + " | ".join(f"{x:,.0f}".replace(",", " ") for x in v) + " | " + " | ".join(conv) + f" | {pct(v[-1] / v[0]) if v[0] else '—'} |")
    print("\nПроверьте: шаги считаются по одной и той же когорте и периоду; иначе конверсии несопоставимы.")


def unit(a):
    ret = [num(x) for x in a.retention.split(",")]
    months = a.months
    surv = [(ret[i] if i < len(ret) else ret[-1]) for i in range(months)]  # после последнего значения — плато
    free = a.free_months
    revenue = sum(a.price * surv[i] for i in range(months) if i >= free) + a.promo_price
    active = sum(surv[i] for i in range(months))
    print(f"## Экономика одного привлечённого за {months} мес.\n")
    print("| Сценарий затрат | Выручка | Затраты на обслуживание | Привлечение | Маржа |")
    print("|---|---|---|---|---|")
    for name, cost in (("переменные (растут с числом активных)", a.cost * active), ("постоянные (не зависят от числа активных)", 0.0)):
        print(f"| {name} | {revenue:,.0f} | {cost:,.0f} | {a.cac:,.0f} | **{revenue - cost - a.cac:+,.0f}** |".replace(",", " "))
    print(f"\nДопущения: цена {a.price:g}, затраты {a.cost:g} в месяц на активного, привлечение {a.cac:g}, бесплатных месяцев {free}, "
          f"доля активных по месяцам: {', '.join(pct(x) for x in surv)} (после последнего заданного значения — плато).")
    print("Если знак маржи в сценариях разный, структура затрат — главный пробел: уточните её до решения. Результат — прогноз, а не факт.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("retention"); p.add_argument("file"); p.add_argument("--period", required=True)
    p.add_argument("--segment", required=True); p.add_argument("--size", required=True); p.add_argument("--kept", required=True)
    p.add_argument("--base"); p.add_argument("--last"); p.set_defaults(f=retention)
    p = sub.add_parser("funnel"); p.add_argument("file"); p.add_argument("--steps", required=True); p.add_argument("--segment")
    p.set_defaults(f=funnel)
    p = sub.add_parser("unit"); p.add_argument("--price", type=float, required=True); p.add_argument("--cost", type=float, default=0.0)
    p.add_argument("--cac", type=float, default=0.0); p.add_argument("--months", type=int, default=6)
    p.add_argument("--retention", required=True, help="доля активных по месяцам через запятую, начиная с первого")
    p.add_argument("--free-months", type=int, default=0); p.add_argument("--promo-price", type=float, default=0.0)
    p.set_defaults(f=unit)
    a = ap.parse_args()
    a.f(a)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
