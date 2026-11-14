"""Расширения CLI «Стратега»: развилки, бюджет, план исследования, сверка цитат, маршрутизация,
диагностика окружения, передача в другую среду, сводная статистика, Jev.

Подключается из strateg.py (register / extra_checks). Только стандартная библиотека.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import strateg as S

CONFIG = S.ROOT / "config" / "routing.json"


# ================================================================ развилки и бюджет
def cmd_gate(a):
    """Подтверждение человека на развилке. В Claude Code хук требует явного клика пользователя на эту команду."""
    st = S.load_state()
    if a.stage not in S.GATE_TEXT:
        S.die(f"развилки есть у этапов: {', '.join(S.GATE_TEXT)}")
    auto = os.environ.get("STRATEG_AUTOGATE") == "1"
    st.setdefault("gates", {})[a.stage] = {"at": S.now(), "choice": a.choice, "auto": auto}
    st.setdefault("human_decisions", []).append(
        {"at": S.now(), "stage": a.stage, "question": f"Развилка: {S.GATE_TEXT[a.stage]}", "answer": a.choice})
    S.save_state(st)
    S.journal(("⚠️ АВТОПОДТВЕРЖДЕНИЕ (тестовый режим, без человека). " if auto else "🚦 Развилка пройдена человеком. ")
              + f"{S.GATE_TEXT[a.stage]}: {a.choice}", stage=a.stage)
    print(f"Развилка «{S.STAGE_NAMES[a.stage]}» пройдена: {a.choice}")


def cmd_budget(a):
    st = S.load_state()
    if a.set is not None:
        st["budget_usd"] = a.set
        S.journal(f"Бюджет изменён: ${a.set:g}")
    if a.mode:
        st["mode"] = a.mode
        S.journal(f"Режим изменён: {a.mode}")
    if a.set is not None or a.mode:
        S.save_state(st)
    tot = S.cost_totals()
    b = st.get("budget_usd")
    print(f"Режим: {st.get('mode', 'full')} | развилки: {', '.join(S.MODES[st.get('mode', 'full')][1])}")
    print(f"Израсходовано ${tot['usd']:.2f}" + (f" из ${b:g} ({100 * tot['usd'] / b:.0f}%)" if b else " (лимит не задан)"))


# ================================================================ план исследования (идемпотентность шагов)
def _plan_path():
    return S.pdir() / "plan.jsonl"


def cmd_plan_add(a):
    rows = S.read_jsonl(_plan_path())
    dup = [r for r in rows if r["question"].strip().lower() == a.q.strip().lower() and r["status"] != "dropped"]
    if dup:
        print(f"{dup[0]['id']} (уже есть в плане, статус {dup[0]['status']})")
        return
    qid = S.next_id(rows, "Q")
    S.append_jsonl(_plan_path(), {"id": qid, "question": a.q, "why": a.why, "method": a.method, "status": "open",
                                  "attempts": 0, "result": None, "claims": [], "created_at": S.now()})
    print(qid)


def _plan_update(qid, fn):
    rows = S.read_jsonl(_plan_path())
    row = next((r for r in rows if r["id"] == qid.upper()), None) or S.die(f"нет пункта плана {qid}")
    msg = fn(row)
    S.write_jsonl(_plan_path(), rows)
    return msg


def cmd_plan_start(a):
    def fn(r):
        if r["status"] == "done":
            return f"{r['id']} уже ВЫПОЛНЕН ({r.get('done_at')}): {r.get('result')}. Не повторяй — используй утверждения {', '.join(r['claims']) or '—'}."
        r["attempts"] += 1
        again = r["status"] == "in_progress"
        r["status"] = "in_progress"
        r["started_at"] = r.get("started_at") or S.now()
        if again:
            return (f"{r['id']} уже был начат {r['started_at']} (попытка {r['attempts']}). Возможно, прошлый запуск прервался: "
                    f"сначала проверь частичный результат — ./strateg search \"{' '.join(r['question'].split()[:4])}\" и journal.md.")
        return f"{r['id']} начат"
    print(_plan_update(a.id, fn))


def cmd_plan_done(a):
    def fn(r):
        r["status"] = "done"
        r["done_at"] = S.now()
        r["result"] = a.result
        r["claims"] = sorted(set(r["claims"]) | set(S.split_ids(a.claims)))
        return f"{r['id']} закрыт"
    print(_plan_update(a.id, fn))
    S.journal(f"План: {a.id.upper()} закрыт — {a.result}")


def cmd_plan_drop(a):
    def fn(r):
        r["status"] = "dropped"
        r["result"] = a.reason
        return f"{r['id']} снят: {a.reason}"
    print(_plan_update(a.id, fn))


def cmd_plan_list(a):
    icons = {"open": "⬜", "in_progress": "▶️", "done": "✅", "dropped": "✖️"}
    rows = S.read_jsonl(_plan_path())
    if not rows:
        print("План пуст. Добавь вопросы: ./strateg plan add --q \"…\" --why \"какой ответ к какому варианту склонит\"")
    for r in rows:
        print(f"{icons[r['status']]} {r['id']} {r['question']}")
        if r.get("why"):
            print(f"     зачем: {r['why']}")
        if r.get("result"):
            print(f"     итог: {r['result']} [{', '.join(r['claims'])}]")


# ================================================================ сверка цитат (детерминированно, бесплатно)
LOCATION_RE = re.compile(r"^\s*(стр\.?|страниц|лист|ячейк|строк|экран|таймкод|слайд|вывод скрипта|см\.)", re.I)
NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")


def norm(text: str) -> str:
    text = text.lower().replace("ё", "е").replace(" ", " ")
    return re.sub(r"\s+", " ", re.sub(r"[^0-9a-zа-я%.,]+", " ", text)).strip()


def source_text(src: dict) -> str | None:
    """Текст источника из локального снимка. Сеть не используется: проверяем только то, что сохранено."""
    cands = []
    for key in ("snapshot", "path"):
        if src.get(key):
            p = Path(src[key])
            cands.append(p if p.is_absolute() else S.ROOT / p)
            cands.append(S.pdir() / "sources" / (p.stem + ".md"))  # результат ./strateg ingest
    cands.append(S.pdir() / "sources" / f"{src['id']}.md")
    for p in cands:
        if p.exists() and p.is_file() and p.suffix.lower() in (".md", ".txt", ".csv", ".tsv", ".json", ".html", ".htm", ".py", ".yml"):
            try:
                return p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
    return None


def check_quote(quote: str, text: str) -> str:
    nt = norm(text)
    parts = [norm(x) for x in re.split(r"\.\.\.|…|\[\.\.\.\]", quote) if len(norm(x)) > 3]
    if not parts:
        return "no_quote"
    if all(part in nt for part in parts):
        return "ok"
    q = norm(quote).split()
    if len(q) < 3:
        return "missing"
    words = nt.split()
    grams = {tuple(words[i:i + 3]) for i in range(len(words) - 2)}
    hit = sum(1 for i in range(len(q) - 2) if tuple(q[i:i + 3]) in grams)
    return "close" if hit / max(len(q) - 2, 1) >= 0.6 else "missing"


def cmd_verify(a):
    claims = S.load_claims()
    sources = {s["id"]: s for s in S.read_jsonl(S.pdir() / "sources.jsonl")}
    texts = {}
    stat = {}
    problems = []
    for c in claims:
        if c["type"] != "fact" or c["status"] in ("retracted", "superseded"):
            continue
        ev = (c.get("evidence") or "").strip().strip("«»\"'")
        src_texts = []
        for sid in c["sources"]:
            if sid in sources:
                if sid not in texts:
                    texts[sid] = source_text(sources[sid])
                if texts[sid]:
                    src_texts.append(texts[sid])
        res = {"at": S.now(), "numbers_missing": []}
        if not ev:
            res["status"] = "no_quote"
        elif LOCATION_RE.match(ev):
            res["status"] = "location"
        elif not src_texts:
            res["status"] = "no_snapshot"
        else:
            order = {"ok": 0, "close": 1, "missing": 2, "no_quote": 3}
            res["status"] = min((check_quote(ev, t) for t in src_texts), key=lambda x: order[x])
        if src_texts:
            joined = norm(" ".join(src_texts)).replace(",", ".")
            for n in NUM_RE.findall(c["text"]):
                n2 = n.replace(",", ".")
                if len(n2) >= 2 and n2 not in joined and n2.rstrip("0").rstrip(".") not in joined:
                    res["numbers_missing"].append(n)
        c["quote_check"] = res
        stat[res["status"]] = stat.get(res["status"], 0) + 1
        if res["status"] in ("missing", "close") or res["numbers_missing"]:
            problems.append(c)
    S.save_claims(claims)
    names = {"ok": "цитата найдена дословно", "close": "близко к тексту (перефраз)", "missing": "цитата НЕ найдена",
             "location": "указано место, не цитата", "no_snapshot": "нет локального снимка источника", "no_quote": "нет цитаты"}
    print("Сверка цитат фактов с сохранёнными снимками источников (без LLM, бесплатно):")
    for k, n in sorted(stat.items(), key=lambda x: -x[1]):
        print(f"  {names[k]}: {n}")
    for c in problems:
        qc = c["quote_check"]
        extra = f"; числа не найдены в источнике: {', '.join(qc['numbers_missing'])}" if qc["numbers_missing"] else ""
        print(f"  ⚠️ {c['id']} [{names[qc['status']]}{extra}] {c['text'][:90]}")
    if problems:
        print(f"→ Передай эти {len(problems)} id субагенту evidence-auditor (haiku): остальные проверены кодом.")
    S.history({"event": "verify", "stat": stat, "problems": [c["id"] for c in problems]})
    S.journal("Сверка цитат: " + ", ".join(f"{names[k]} — {n}" for k, n in stat.items()))


def parse_date(v: str | None):
    if not v:
        return None
    m = re.match(r"(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?", str(v).strip())
    if not m:
        return None
    try:
        return dt.date(int(m.group(1)), int(m.group(2) or 12), int(m.group(3) or 28))
    except ValueError:
        return None


def stale_sources(sources: dict, days: int = 730) -> dict:
    out = {}
    for sid, s in sources.items():
        d = parse_date(s.get("published"))
        if d and (dt.date.today() - d).days > days:
            out[sid] = d.isoformat()
    return out


def extra_checks(claims, sources, errors, warns):
    """Дополнительные проверки для ./strateg check."""
    facts = [c for c in claims if c["type"] == "fact" and c["status"] not in ("retracted", "superseded")]
    unverified = [c["id"] for c in facts if "quote_check" not in c]
    if facts and len(unverified) > len(facts) / 2:
        warns.append(f"{len(unverified)} фактов не проходили сверку цитат — запусти ./strateg verify")
    for c in facts:
        qc = c.get("quote_check") or {}
        if qc.get("status") == "missing":
            warns.append(f"{c['id']}: цитата не найдена в снимке источника")
        if qc.get("numbers_missing"):
            warns.append(f"{c['id']}: числа {', '.join(qc['numbers_missing'])} не найдены в источнике")
    old = stale_sources(sources)
    only_old = [c["id"] for c in facts if c["sources"] and all(s in old for s in c["sources"])]
    if only_old:
        warns.append(f"факты {', '.join(only_old)} опираются только на источники старше 2 лет "
                     f"({', '.join(f'{k}: {v[:7]}' for k, v in old.items())}) — обнови или понизь уверенность")
    st = S.load_state()
    for stage, status in st["stages"].items():
        if status == "done" and stage in S.MODES[st.get("mode", "full")][1]:
            g = st.get("gates", {}).get(stage)
            if not g:
                errors.append(f"этап «{S.STAGE_NAMES[stage]}» закрыт без подтверждения человека")
            elif g.get("auto"):
                warns.append(f"развилка «{S.STAGE_NAMES[stage]}» пройдена автоматически (тестовый режим)")
    open_q = [q["id"] for q in S.read_jsonl(_plan_path()) if q["status"] in ("open", "in_progress")]
    if open_q and st["stages"].get("research") == "done":
        warns.append(f"исследование закрыто, но в плане остались вопросы: {', '.join(open_q)} — закрой или сними (plan drop) с причиной")


# ================================================================ маршрутизация
def load_routing() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def classify_task(text: str, cfg: dict) -> tuple[str, str]:
    """Тип задачи: Jev (если есть ключ) → правила по ключевым словам. Возвращает (тип, чем определено)."""
    tasks = cfg["tasks"]
    if text in tasks:
        return text, "указан явно"
    if os.environ.get("TYPESAFE_API_KEY"):
        try:
            sys.path.insert(0, str(S.ROOT / "scripts" / "tools"))
            import jev
            crit = {k: f"{v['how']}; признаки: {', '.join(v['keywords'])}" for k, v in tasks.items()}
            ans = jev.ask({"task": text}, {"kind": {"type": "choice", "instructions": "К какому типу работы относится задача?",
                                                    "criteria": crit}})
            kind = ans["answers"]["kind"]["choice"]
            if kind in tasks:
                return kind, "Jev"
        except Exception as e:  # noqa: BLE001 — любая ошибка Jev → запасной путь
            print(f"(Jev недоступен: {e}; использую правила)", file=sys.stderr)
    low = text.lower()
    best = max(tasks, key=lambda k: sum(1 for w in tasks[k]["keywords"] if w in low))
    if not any(w in low for w in tasks[best]["keywords"]):
        best = "orchestration"
    return best, "правила"


def cmd_route(a):
    cfg = load_routing()
    if a.list or not a.task:
        print("Лестница: " + " → ".join(cfg["ladder"]) + "  (берём самую дешёвую ступень, проходящую проверку)")
        for k, v in cfg["tasks"].items():
            print(f"  {k:20} старт: {v['start']:7} {v['how']}")
        return
    text = " ".join(a.task)
    kind, by = classify_task(text, cfg)
    t = cfg["tasks"][kind]
    print(f"Тип задачи: {kind} (определено: {by})")
    print(f"Исполнитель: {t['start']} — {t['how']}")
    for key, label in [("then", "Затем"), ("fallback", "Если недоступно"), ("escalate_to", "При провале проверки поднять до"),
                       ("check", "Проверка результата")]:
        if t.get(key):
            print(f"{label}: {t[key]}")
    if S.active_slug(required=False):
        S.append_jsonl(S.pdir() / "routing.jsonl", {"at": S.now(), "event": "route", "task": text, "type": kind,
                                                    "start": t["start"], "by": by})


def cmd_escalate(a):
    S.append_jsonl(S.pdir() / "routing.jsonl", {"at": S.now(), "event": "escalate", "task": a.task, "from": a.frm,
                                                "to": a.to, "reason": a.reason})
    S.journal(f"Эскалация исполнителя: {a.task}: {a.frm} → {a.to} ({a.reason})")
    print(f"Записано: {a.frm} → {a.to}")


# ================================================================ статистика прозрачности
def collect_stats(slug=None) -> dict:
    d = S.pdir(slug)
    st = S.load_state(slug)
    tot = S.cost_totals(slug)
    acts = S.read_jsonl(d / "activity.jsonl")
    tools: dict[str, int] = {}
    for x in acts:
        t = x["tool"]
        t = "MCP: " + t.split("__")[1] if t.startswith("mcp__") else t
        tools[t] = tools.get(t, 0) + 1
    routing = S.read_jsonl(d / "routing.jsonl")
    claims = S.read_jsonl(d / "claims.jsonl")
    qc: dict[str, int] = {}
    for c in claims:
        if c.get("quote_check"):
            qc[c["quote_check"]["status"]] = qc.get(c["quote_check"]["status"], 0) + 1
    hist = S.read_jsonl(d / "history.jsonl")
    return {
        "usd": tot["usd"], "minutes": tot["seconds"] / 60, "sessions": tot["sessions"], "model_calls": tot["turns"],
        "by_model": {m: round(v["usd"], 3) for m, v in tot["by_model"].items()}, "by_stage": tot["by_stage"],
        "budget_usd": st.get("budget_usd"), "mode": st.get("mode", "full"),
        "tool_calls": len(acts), "tools": tools, "tool_errors": sum(1 for x in acts if x.get("error")),
        "subagent_calls": tools.get("Agent", 0) + tools.get("Task", 0),
        "human_gates": len(st.get("gates", {})), "human_answers": len(st.get("human_decisions", [])),
        "escalations": sum(1 for r in routing if r["event"] == "escalate"),
        "revisions": sum(1 for h in hist if h["event"] in ("revise", "source_set")),
        "quote_check": qc, "checkpoints": len(st.get("checkpoints", [])),
    }


def cmd_stats(a):
    s = collect_stats()
    if a.json:
        print(json.dumps(s, ensure_ascii=False, indent=2))
        return
    print(f"Режим: {s['mode']} | стоимость ${s['usd']:.2f}" + (f" из ${s['budget_usd']:g}" if s["budget_usd"] else "")
          + f" | время {s['minutes']:.0f} мин | сессий {s['sessions']} | обращений к моделям {s['model_calls']}")
    print("По моделям: " + (", ".join(f"{m} ${v}" for m, v in s["by_model"].items()) or "—"))
    print(f"Действий инструментов: {s['tool_calls']} (ошибок {s['tool_errors']}), вызовов субагентов: {s['subagent_calls']}")
    top = sorted(s["tools"].items(), key=lambda x: -x[1])[:8]
    print("  " + ", ".join(f"{k} {v}" for k, v in top))
    print(f"Участие человека: развилок пройдено {s['human_gates']}, ответов записано {s['human_answers']}")
    print(f"Эскалаций исполнителя: {s['escalations']} | пересмотров: {s['revisions']} | контрольных точек: {s['checkpoints']}")
    if s["quote_check"]:
        print("Сверка цитат: " + ", ".join(f"{k} {v}" for k, v in s["quote_check"].items()))


# ================================================================ передача в другую среду
def cmd_handoff(a):
    import contextlib
    import io
    st = S.load_state()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        S.main(["status", "--tail", "12"])
        print("\n## План исследования")
        S.main(["plan", "list"])
    slug = S.active_slug()
    text = f"""# Передача проекта «{st['title']}» в другую среду

Этот файл — самодостаточная инструкция для ИИ-агента в любой среде (Cursor, Codex, Claude Code, GigaCode, обычный чат
с доступом к файлам). Создан {S.now()}.

## Что сделать сначала
1. Прочитай `AGENTS.md` в корне репозитория — это твои постоянные правила.
2. Процедура текущего этапа: `.claude/skills/{st['stage']}/SKILL.md`. Роли помощников: `.claude/agents/*.md`
   (если в среде нет субагентов — выполняй их роли сам, соблюдая их правила).
3. Все записи состояния — только командами `./strateg …` (или `python scripts/strateg.py …`). Проверь окружение: `./strateg doctor`.
4. Если в среде нет хуков: сам запускай `./strateg status` в начале и `./strateg checkpoint "…"` в конце каждого шага;
   развилки подтверждай только после явного ответа человека (`./strateg gate <этап> --choice "…"`).

## Где мы сейчас
```
{buf.getvalue().strip()}
```

## Файлы проекта
- Состояние и реестры: `projects/{slug}/state.json`, `claims.jsonl`, `sources.jsonl`, `decisions.jsonl`, `plan.jsonl`
- Материалы: `projects/{slug}/inputs/`, снимки источников: `projects/{slug}/sources/`
- Результаты: `projects/{slug}/outputs/`
- Журнал: `projects/{slug}/journal.md`

Продолжай со «Следующего шага». Не переделывай завершённые этапы и закрытые пункты плана.
"""
    out = S.pdir() / "HANDOFF.md"
    out.write_text(text, encoding="utf-8")
    S.journal("Создан файл передачи HANDOFF.md")
    print(out)


# ================================================================ диагностика окружения
def _ver(cmd):
    try:
        exe = shutil.which(cmd[0])
        if not exe:
            return None
        return subprocess.run([exe] + cmd[1:], capture_output=True, text=True, timeout=20).stdout.strip().splitlines()[0]
    except Exception:  # noqa: BLE001
        return None


def cmd_doctor(a):
    ok = True
    rows = []

    def row(level, name, detail):
        rows.append((level, name, detail))

    v = sys.version_info
    row("✅" if v >= (3, 9) else "❌", "Python", f"{v.major}.{v.minor}.{v.micro} ({sys.executable})")
    ok &= v >= (3, 9)
    git = _ver(["git", "--version"])
    row("✅" if git else "⚠️", "git", git or "не найден — нужен только для обновления и истории")
    node = _ver(["node", "--version"])
    node_ok = bool(node) and int(re.sub(r"\D", "", node.split(".")[0]) or 0) >= 18
    row("✅" if node_ok else "⚠️", "Node.js 18+", node or "не найден — без него не работают MCP-серверы (браузер, реестр); остальное работает")
    claude = _ver(["claude", "--version"])
    row("✅" if claude else "⚠️", "Claude Code", claude or "не найден в PATH — нужен для работы агента (или откройте папку в Cursor)")
    for mod, why in [("markitdown", "лучшее чтение PDF/DOCX/PPTX"), ("pypdf", "чтение PDF"), ("openpyxl", "чтение XLSX"),
                     ("pandas", "анализ данных")]:
        try:
            __import__(mod)
            row("✅", f"python: {mod}", why)
        except ImportError:
            row("⚠️", f"python: {mod}", f"не установлен ({why}) — необязательно: pip install -r requirements.txt")
    for f in [".claude/settings.json", ".mcp.json", "config/routing.json"]:
        try:
            json.loads((S.ROOT / f).read_text(encoding="utf-8"))
            row("✅", f, "корректный JSON")
        except Exception as e:  # noqa: BLE001
            row("❌", f, f"ошибка: {e}")
            ok = False
    hooks = ["session_start.py", "guard.py", "activity_log.py", "cost_tracker.py", "py.sh"]
    miss = [h for h in hooks if not (S.ROOT / ".claude" / "hooks" / h).exists()]
    row("✅" if not miss else "❌", "хуки", "на месте" if not miss else f"нет файлов: {', '.join(miss)}")
    ok &= not miss
    try:
        S.PROJECTS.mkdir(parents=True, exist_ok=True)
        t = S.PROJECTS / ".write_test"
        t.write_text("ok", encoding="utf-8")
        t.unlink()
        row("✅", "папка projects/", "запись разрешена")
    except OSError as e:
        row("❌", "папка projects/", f"нет записи: {e}")
        ok = False
    row("✅" if os.environ.get("TYPESAFE_API_KEY") else "ℹ️", "Jev (необязательно)",
        "ключ найден" if os.environ.get("TYPESAFE_API_KEY") else "ключ TYPESAFE_API_KEY не задан — используется запасной путь (haiku)")
    slug = S.active_slug(required=False)
    row("ℹ️", "активный проект", slug or "нет — начните с /strateg <ваша задача>")
    for level, name, detail in rows:
        print(f"{level} {name:22} {detail}")
    print("\nИтог: " + ("всё необходимое есть." if ok else "есть критичные проблемы (❌) — исправьте их."))
    if not ok:
        sys.exit(1)


# ================================================================ Jev
def cmd_jev(a):
    sys.path.insert(0, str(S.ROOT / "scripts" / "tools"))
    import jev
    jev.run(a, S)


# ================================================================ регистрация
def register(sub):
    p = sub.add_parser("gate", help="развилка: подтверждение человека (frame / options / strategy)")
    p.add_argument("stage"); p.add_argument("--choice", required=True); p.set_defaults(f=cmd_gate)
    p = sub.add_parser("budget", help="бюджет и режим")
    p.add_argument("--set", type=float); p.add_argument("--mode", choices=list(S.MODES)); p.set_defaults(f=cmd_budget)

    pl = sub.add_parser("plan", help="план исследования: вопросы и их статус").add_subparsers(dest="pc", required=True)
    p = pl.add_parser("add"); p.add_argument("--q", required=True); p.add_argument("--why"); p.add_argument("--method")
    p.set_defaults(f=cmd_plan_add)
    p = pl.add_parser("start"); p.add_argument("id"); p.set_defaults(f=cmd_plan_start)
    p = pl.add_parser("done"); p.add_argument("id"); p.add_argument("--result", required=True)
    p.add_argument("--claims", action="append"); p.set_defaults(f=cmd_plan_done)
    p = pl.add_parser("drop"); p.add_argument("id"); p.add_argument("--reason", required=True); p.set_defaults(f=cmd_plan_drop)
    p = pl.add_parser("list"); p.set_defaults(f=cmd_plan_list)

    p = sub.add_parser("verify", help="сверка цитат фактов со снимками источников (без LLM)"); p.set_defaults(f=cmd_verify)
    p = sub.add_parser("route", help="какой исполнитель/модель для задачи"); p.add_argument("task", nargs="*")
    p.add_argument("--list", action="store_true"); p.set_defaults(f=cmd_route)
    p = sub.add_parser("escalate", help="записать исправление выбора исполнителя")
    p.add_argument("task"); p.add_argument("--from", dest="frm", required=True); p.add_argument("--to", required=True)
    p.add_argument("--reason", required=True); p.set_defaults(f=cmd_escalate)
    p = sub.add_parser("stats", help="сводка прозрачности: время, деньги, действия, участие человека")
    p.add_argument("--json", action="store_true"); p.set_defaults(f=cmd_stats)
    p = sub.add_parser("handoff", help="файл передачи проекта в другую среду"); p.set_defaults(f=cmd_handoff)
    p = sub.add_parser("doctor", help="проверка окружения"); p.set_defaults(f=cmd_doctor)

    j = sub.add_parser("jev", help="Jev — модель-«решатель» (необязательно)").add_subparsers(dest="jc", required=True)
    p = j.add_parser("types", help="проверить типы утверждений и опору на цитату"); p.add_argument("--ids", action="append")
    p.set_defaults(f=cmd_jev)
    p = j.add_parser("score", help="оценить варианты из outputs/03_options.md по критериям")
    p.add_argument("--criteria", required=True, help="критерии через ';'"); p.set_defaults(f=cmd_jev)
    p = j.add_parser("ask", help="произвольный вопрос"); p.add_argument("--state", required=True)
    p.add_argument("--question", required=True); p.add_argument("--type", choices=["noul", "choice", "score"], default="noul")
    p.add_argument("--options", help="для choice/score: варианты через ';'"); p.set_defaults(f=cmd_jev)
