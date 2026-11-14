"""Orbita Kino: M3-удержание, причины ухода, юнит-экономика акции.
Источники: S-001 cohorts.csv, S-002 exit_survey.csv, S-003 support_tickets.csv, S-004 memo (допущения, надёжность E).
M3 = paying_day90 / new_subscribers (знаменатель — все новые, не активные на 30-й день).
Последняя завершённая когорта: 2026-07 (пустые paying_day90 у 2026-08, 2026-09)."""
import csv, os
B = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "inputs")
def rd(n): return list(csv.DictReader(open(os.path.join(B, n), encoding="utf-8")))
def num(x): return None if x in ("", None) else int(x)
co = rd("cohorts.csv")
for r in co:
    for k in ("new_subscribers", "active_day30", "paying_day90"): r[k] = num(r[k])

print("== 0. Проверки данных ==")
keys = [(r["cohort_month"], r["channel"]) for r in co]
print("дубли ключей:", len(keys) - len(set(keys)))
print("строк:", len(co), "| пропуски paying_day90:", sum(r["paying_day90"] is None for r in co),
      "(месяцы:", sorted({r["cohort_month"] for r in co if r["paying_day90"] is None}), ")")
print("paying>active30>new нарушений:", sum(not (r["paying_day90"] is None or r["paying_day90"] <= r["active_day30"] <= r["new_subscribers"]) for r in co))
print("Проверка полноты: когорта сентября (на дату 2026-11-14) 90 дней не прошла полностью -> считаем только до 2026-07 как завершённые по данным; авг-сен пусто.")

months = sorted({r["cohort_month"] for r in co})
def agg(m, ch=None):
    rows = [r for r in co if r["cohort_month"] == m and (ch is None or r["channel"] == ch)]
    return sum(r["new_subscribers"] for r in rows), sum(r["active_day30"] for r in rows), (None if any(r["paying_day90"] is None for r in rows) or not rows else sum(r["paying_day90"] for r in rows))
print("\n== 1. M3 по месяцам и каналам, % (paying90/new) ==")
print("месяц   | всего new | M3 общий | bundle | direct | promo | доля promo в new")
for m in months:
    n, a, p = agg(m)
    s = lambda ch: (lambda x: "  -  " if x[0] == 0 or x[2] is None else f"{100*x[2]/x[0]:5.1f}")(agg(m, ch)) if any(r["cohort_month"]==m and r["channel"]==ch for r in co) else "  -  "
    tot = "  -  " if p is None else f"{100*p/n:5.1f}"
    print(f"{m} | {n:9d} | {tot:>8} | {s('bundle')} | {s('direct')} | {s('promo')} | {100*agg(m,'promo')[0]/n:5.1f}")

print("\n== 2. Разложение изменения M3: 2026-01 -> 2026-07 ==")
A, Z = "2026-01", "2026-07"
chs = ["bundle", "direct", "promo"]
def shr(m, ch):
    n = agg(m)[0]; return agg(m, ch)[0] / n if agg(m, ch)[0] else 0.0
def ret(m, ch):
    x = agg(m, ch); return x[2] / x[0] if x[0] else None
tot0 = agg(A)[2] / agg(A)[0]; tot1 = agg(Z)[2] / agg(Z)[0]
print(f"M3 общий: {A}={100*tot0:.1f}%  {Z}={100*tot1:.1f}%  изменение={100*(tot1-tot0):.1f} п.п.")
# Шепли-подобное (среднее двух порядков) для 'состав' и 'внутри каналов'; promo в янв: доля 0, удержания нет -> ставим удержание янв promo = удержание июль (нейтрально)
comp = within = 0.0
print("канал  | доля янв | доля июл | M3 янв | M3 июл | вклад состава, п.п. | вклад внутри, п.п.")
for ch in chs:
    s0, s1 = shr(A, ch), shr(Z, ch)
    r1 = ret(Z, ch); r0 = ret(A, ch) if ret(A, ch) is not None else r1
    c = 0.5 * (s1 - s0) * (r0 + r1)
    w = 0.5 * (r1 - r0) * (s0 + s1)
    comp += c; within += w
    print(f"{ch:6s} | {100*s0:7.1f}% | {100*s1:7.1f}% | {100*r0:5.1f}% | {100*r1:5.1f}% | {100*c:+19.2f} | {100*w:+.2f}")
print(f"ИТОГО: состав {100*comp:+.2f} п.п., внутри каналов {100*within:+.2f} п.п., сумма {100*(comp+within):+.2f} (факт {100*(tot1-tot0):+.2f})")
print("Контрфактически: M3 июля при составе января (bundle 57.1%/direct 42.9%) и июльских удержаниях bundle/direct: "
      f"{100*(shr(A,'bundle')*ret(Z,'bundle')+shr(A,'direct')*ret(Z,'direct')):.1f}%")
print("M3 только bundle+direct, янв vs июл: "
      f"{100*(agg(A,'bundle')[2]+agg(A,'direct')[2])/(agg(A,'bundle')[0]+agg(A,'direct')[0]):.1f}% vs "
      f"{100*(agg(Z,'bundle')[2]+agg(Z,'direct')[2])/(agg(Z,'bundle')[0]+agg(Z,'direct')[0]):.1f}%")
print("Direct M3: янв-май " + ", ".join(f"{m}:{100*ret(m,'direct'):.1f}" for m in months[:5]) + " | после повышения цены (с 1 июня, по S-004): " + ", ".join(f"{m}:{100*ret(m,'direct'):.1f}" for m in months[5:7]))
print("Promo M3: " + ", ".join(f"{m}:{100*ret(m,'promo'):.1f}" for m in months[3:7]))
print("Promo active30 (доля new): " + ", ".join(f"{m}:{100*agg(m,'promo')[1]/agg(m,'promo')[0]:.1f}" for m in months[3:]))
print("Прим.: общий M3 3 мес. (мар) = %.1f%%, т.е. 'около 60%%' верно для янв-мар, 'около 35%%' для июн-июл." % (100*agg('2026-03')[2]/agg('2026-03')[0]))

print("\n== 2b. Расхождение с запиской S-004 (52% для июня) ==")
n, a, p = agg("2026-06")
print(f"Июнь по когортам: M3 = {p}/{n} = {100*p/n:.1f}%; active30/new = {100*a/n:.1f}%")
bd = agg("2026-06","bundle"), agg("2026-06","direct"), agg("2026-06","promo")
print(f"без promo: {100*(bd[0][2]+bd[1][2])/(bd[0][0]+bd[1][0]):.1f}%; простое среднее трёх каналов: {100*sum(x[2]/x[0] for x in bd)/3:.1f}%; "
      f"(active30+paying)/2 общий: {100*(a/n+p/n)/2:.1f}%")
print("Что даёт 52%? Подбор: доля 'не отключена подписка' между active30 (86.0%) и paying90 (34.7%) -> 52% не воспроизводится из S-001 ни одним простым способом; "
      "по методике дашборда (S-004 п.4) учитываются пауза и неуспешный платёж — т.е. это не 'платящие'. Расхождение = разница определений (гипотеза), проверить нельзя без выгрузки статусов.")

print("\n== 3. Опрос ушедших (S-002) ==")
sv = rd("exit_survey.csv")
tot = {}
for r in sv: tot[r["channel"]] = tot.get(r["channel"], 0) + int(r["responses"])
print("канал  | причина | ответов | доля, %")
for r in sv:
    print(f"{r['channel']:6s} | {r['reason']} | {r['responses']} | {100*int(r['responses'])/tot[r['channel']]:.1f}")
print("Ответов по каналам:", tot, "всего", sum(tot.values()))
cont = {r["channel"]: int(r["responses"]) for r in sv if "контент" in r["reason"]}
print("Доля 'Мало нового контента': " + ", ".join(f"{c}={100*cont[c]/tot[c]:.1f}%" for c in cont) + f", все={100*sum(cont.values())/sum(tot.values()):.1f}%")
pr = {r["reason"]: int(r["responses"]) for r in sv if r["channel"] == "promo"}
print(f"promo: условия акции/списания (полная цена + не знал о списаниях) = {100*(552+324)/tot['promo']:.1f}%")
print("Ограничение: у опроса нет знаменателя (сколько ушедших получили/ответили), категории по каналам не совпадают (в direct нет 'акции', в bundle нет 'цены').")

print("\n== 4. Обращения в поддержку (S-003) ==")
tk = rd("support_tickets.csv")
cats = sorted({r["category"] for r in tk}); ms = sorted({r["month"] for r in tk})
print("месяц   | " + " | ".join(cats))
T = {(r["month"], r["category"]): int(r["tickets"]) for r in tk}
for m in ms: print(m, "|", " | ".join(f"{T[(m,c)]:6d}" for c in cats))
for c in cats:
    base = sum(T[(m,c)] for m in ms[:5]) / 5
    print(f"{c}: среднее янв-май={base:.0f}; июн={T[('2026-06',c)]}; сен={T[('2026-09',c)]} (x{T[('2026-09',c)]/base:.1f} к янв-май)")
print("Корреляция по времени, не причинность: рост списаний совпал с ростом promo-когорт (май 5000, июн 7000... сен 8000 новых); воспроизведение стабильно (~400).")
print("Ограничение: тикеты без разреза по каналу; рост также может отражать общий рост базы.")

print("\n== 5. Юнит-экономика на 1 привлечённого за 6 месяцев (допущения S-004, надёжность E) ==")
PRICE_NEW, PRICE_OLD, COST, PARTNER, TRANSFER = 399, 299, 120, 150, 150
print(f"Допущения: цена direct {PRICE_OLD}->{PRICE_NEW} с 1.06; акция 1 ₽ за 3 мес. (трактуем 1 ₽ всего), далее {PRICE_NEW}; партнёру {PARTNER} ₽ разово; затраты {COST} ₽/мес на активного.")
print("Модель удержания (допущение, данных после дня 90 нет): м1 = 100% активны; м2 = доля active30; м3-м6 = доля paying90 (плато). Выручка direct с м1, promo с м4.")
def ue(a30, m3, promo, price=PRICE_NEW):
    act = [1.0, a30, m3, m3, m3, m3]
    cost = COST * sum(act)
    if promo:
        rev = 1 + price * sum(act[3:]); partner = PARTNER
    else:
        rev = price * sum(act); partner = 0
    return rev, cost, partner, rev - cost - partner
for lab, m, ch, promo in [("promo, когорта 2026-07", Z, "promo", True), ("promo, 2026-06", "2026-06", "promo", True), ("direct, 2026-07 (цена 399)", Z, "direct", False), ("direct, 2026-05 (цена 299)", "2026-05", "direct", False)]:
    x = agg(m, ch); a30 = x[1] / x[0]; m3 = x[2] / x[0]
    price = PRICE_OLD if m == "2026-05" else PRICE_NEW
    r, c, p, mg = ue(a30, m3, promo, price)
    print(f"{lab}: a30={100*a30:.1f}% M3={100*m3:.1f}% | выручка {r:.0f} | затраты контента {c:.0f} | партнёру {p} | маржа {mg:.0f} ₽")
x = agg(Z, "promo"); a30 = x[1] / x[0]
lo, hi = 0.0, a30
for _ in range(60):
    mid = (lo + hi) / 2
    if ue(a30, mid, True)[3] < 0: lo = mid
    else: hi = mid
print(f"Точка безубыточности акции: M3 ≈ {100*hi:.1f}% (при a30={100*a30:.1f}%, цене {PRICE_NEW}); факт promo M3 = {100*x[2]/x[0]:.1f}%")
for mm in (0.10, 0.14, 0.20, 0.30, 0.40, 0.50, 0.53, 0.60):
    print(f"  M3={100*mm:.0f}% -> маржа акции {ue(a30, mm, True)[3]:+.0f} ₽/чел")
print(f"Суммарно по июльской когорте promo ({x[0]} чел): {x[0]*ue(a30, x[2]/x[0], True)[3]/1e6:+.2f} млн ₽ за 6 мес.")
print("Горизонт: суммарные 6 месяцев по когорте июль ещё не завершены; модель плато — оптимистична или нейтральна, реальный отток после мес.4 неизвестен.")
print("Нюансы: трансфер bundle 150 ₽ в этой модели не используется; каннибализация direct акцией неизвестна; 52% дашборда (S-004) близко к точке безубыточности — но факт promo M3 14%.")
