#!/usr/bin/env python3
"""strateg — CLI «Стратега»: состояние проекта, реестр источников и утверждений,
решения, пересмотр выводов, стоимость и проверки целостности.

Только стандартная библиотека Python 3.9+. Запуск: python3 scripts/strateg.py <команда>
Все данные проекта лежат в projects/<slug>/ в человекочитаемых JSON/JSONL/MD файлах.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
if __name__ == "__main__":
    sys.modules.setdefault("strateg", sys.modules["__main__"])  # чтобы strateg_ext видел тот же модуль

ROOT = Path(__file__).resolve().parent.parent
PROJECTS = Path(os.environ.get("STRATEG_PROJECTS_DIR") or ROOT / "projects")
ACTIVE_FILE = PROJECTS / ".active"

STAGES = [
    ("frame", "Постановка задачи"),
    ("collect", "Сбор материалов"),
    ("research", "Исследование"),
    ("options", "Варианты"),
    ("strategy", "Стратегия и план проверки"),
    ("decide", "Решение и сопровождение"),
]
STAGE_KEYS = [s[0] for s in STAGES]
STAGE_NAMES = dict(STAGES)

# Тип утверждения -> префикс id. Тип определяет, как утверждение можно использовать.
CLAIM_TYPES = {
    "fact": "F",            # проверяемый факт, обязателен источник
    "hypothesis": "H",      # предположение, требующее проверки
    "forecast": "P",        # прогноз / оценка будущего
    "assumption": "A",      # допущение, принятое для расчёта
    "synthetic": "Y",       # вывод из беседы с модельным респондентом — НЕ доказательство
    "gap": "G",             # чего мы не знаем и почему
    "conclusion": "K",      # вывод агента из других утверждений
    "recommendation": "R",  # рекомендация
}
TYPE_RU = {
    "fact": "факт", "hypothesis": "гипотеза", "forecast": "прогноз",
    "assumption": "допущение", "synthetic": "синтетика", "gap": "пробел",
    "conclusion": "вывод", "recommendation": "рекомендация",
}
CLAIM_STATUSES = ["active", "needs_review", "contested", "retracted", "superseded", "confirmed"]
RELIABILITY = {
    "A": "первичные данные / официальная отчётность / собственные материалы",
    "B": "авторитетное исследование, регулятор, крупное отраслевое издание",
    "C": "СМИ, отраслевые блоги, обзоры с указанием методики",
    "D": "мнения, форумы, отзывы, маркетинговые материалы",
    "E": "непроверяемо / неизвестное происхождение",
}
MODES = {
    # режим -> (бюджет по умолчанию в $ по ценам API, этапы с обязательным подтверждением человека)
    "full": (8.0, ["frame", "options", "strategy"]),
    "express": (2.0, ["options"]),
}
GATE_TEXT = {
    "frame": "постановка задачи утверждена человеком",
    "options": "человек выбрал вариант(ы) для проработки",
    "strategy": "человек утвердил стратегию и рекомендацию",
}
ID_RE = re.compile(r"\b([FHPAYGKRSD])-(\d{3,})\b")


# ---------------------------------------------------------------- утилиты
def now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def today() -> str:
    return dt.date.today().isoformat()


def die(msg: str, code: int = 1):
    print(f"Ошибка: {msg}", file=sys.stderr)
    sys.exit(code)


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    tmp.replace(path)  # атомарная запись: сбой не оставит полупустой файл


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def write_jsonl(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp.replace(path)


def append_jsonl(path: Path, row: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def split_ids(values) -> list[str]:
    out: list[str] = []
    for v in values or []:
        for part in re.split(r"[,\s]+", v):
            if part:
                out.append(part.strip().upper())
    return out


# ---------------------------------------------------------------- проект
def active_slug(required: bool = True) -> str | None:
    env = os.environ.get("STRATEG_PROJECT")
    if env:
        return env
    if ACTIVE_FILE.exists():
        slug = ACTIVE_FILE.read_text(encoding="utf-8").strip()
        if slug and (PROJECTS / slug).exists():
            return slug
    if required:
        die("нет активного проекта. Создайте: strateg init <slug> --title \"...\" или выберите: strateg use <slug>")
    return None


def pdir(slug: str | None = None) -> Path:
    return PROJECTS / (slug or active_slug())


def load_state(slug: str | None = None) -> dict:
    st = read_json(pdir(slug) / "state.json")
    if st is None:
        die(f"нет state.json в проекте {slug or active_slug()}")
    return st


def save_state(st: dict, slug: str | None = None):
    st["updated_at"] = now()
    write_json(pdir(slug) / "state.json", st)


def journal(text: str, slug: str | None = None, stage: str | None = None):
    p = pdir(slug) / "journal.md"
    st = read_json(pdir(slug) / "state.json", {})
    stage = stage or st.get("stage", "")
    with p.open("a", encoding="utf-8") as f:
        f.write(f"- `{now()}` **[{STAGE_NAMES.get(stage, stage)}]** {text}\n")


def next_id(rows: list[dict], prefix: str) -> str:
    n = 0
    for r in rows:
        m = re.match(rf"{prefix}-(\d+)$", r.get("id", ""))
        if m:
            n = max(n, int(m.group(1)))
    return f"{prefix}-{n + 1:03d}"


# ---------------------------------------------------------------- команды: проект
def cmd_init(a):
    slug = a.slug
    if not re.match(r"^[a-z0-9][a-z0-9_-]*$", slug):
        die("slug: латиница в нижнем регистре, цифры, '-' или '_'")
    d = PROJECTS / slug
    if d.exists():
        die(f"проект {slug} уже существует")
    for sub in ["inputs", "sources", "work", "outputs", "interviews", "cost"]:
        (d / sub).mkdir(parents=True, exist_ok=True)
    st = {
        "project": slug,
        "title": a.title or slug,
        "created_at": now(),
        "updated_at": now(),
        "mode": a.mode,
        "budget_usd": a.budget if a.budget is not None else MODES[a.mode][0],
        "gates": {},
        "stage": "frame",
        "stages": {k: ("in_progress" if k == "frame" else "pending") for k in STAGE_KEYS},
        "brief": {
            "question": a.question or "",
            "goal": "", "recipient": "", "decision_to_make": "",
            "constraints": [], "available_data": [], "deadline": "",
            "success_criteria": "",
        },
        "open_questions": [],
        "next_action": "Уточнить постановку задачи с пользователем (skill: frame)",
        "checkpoints": [],
        "human_decisions": [],
    }
    write_json(d / "state.json", st)
    for f in ["sources.jsonl", "claims.jsonl", "decisions.jsonl", "history.jsonl", "activity.jsonl", "plan.jsonl",
              "routing.jsonl"]:
        (d / f).touch()
    (d / "journal.md").write_text(f"# Журнал проекта «{st['title']}»\n\n", encoding="utf-8")
    (d / "inputs" / "README.md").write_text(
        "Сюда кладите исходные материалы: документы, таблицы, скриншоты, расшифровки интервью.\n"
        "Только открытые данные и собственные материалы. Без персональных данных.\n", encoding="utf-8")
    ACTIVE_FILE.parent.mkdir(parents=True, exist_ok=True)
    ACTIVE_FILE.write_text(slug, encoding="utf-8")
    journal("Проект создан" + (f". Исходный запрос: «{a.question}»" if a.question else ""), slug)
    print(f"Создан проект {slug} и сделан активным: {d}")


def cmd_use(a):
    if not (PROJECTS / a.slug).exists():
        die(f"проекта {a.slug} нет")
    ACTIVE_FILE.write_text(a.slug, encoding="utf-8")
    print(f"Активный проект: {a.slug}")


def cmd_list(a):
    act = active_slug(required=False)
    if not PROJECTS.exists():
        print("Проектов нет")
        return
    dirs = sorted(p for p in PROJECTS.iterdir() if p.is_dir() and (p / "state.json").exists())
    if not dirs:
        print("Проектов нет")
    for d in dirs:
        st = read_json(d / "state.json")
        if not st:
            continue
        mark = "*" if d.name == act else " "
        print(f"{mark} {d.name:24} {STAGE_NAMES.get(st['stage'], st['stage']):28} {st.get('title', '')}")


def progress_bar(st: dict) -> str:
    icons = {"done": "✅", "in_progress": "▶️", "pending": "⬜", "skipped": "⏭️"}
    return " → ".join(f"{icons.get(st['stages'].get(k), '⬜')} {n}" for k, n in STAGES)


def cost_totals(slug: str | None = None) -> dict:
    tot = {"usd": 0.0, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "sessions": 0,
           "by_model": {}, "turns": 0, "by_stage": {}, "seconds": 0.0}
    for f in sorted((pdir(slug) / "cost").glob("*.json")):
        c = read_json(f, {})
        if "by_model" not in c:
            continue
        tot["sessions"] += 0 if c.get("kind") == "external" else 1
        for stg, usd in c.get("by_stage", {}).items():
            tot["by_stage"][stg] = tot["by_stage"].get(stg, 0.0) + usd
        if c.get("first_at") and c.get("last_at"):
            try:
                tot["seconds"] += (dt.datetime.fromisoformat(c["last_at"].replace("Z", "+00:00")) -
                                   dt.datetime.fromisoformat(c["first_at"].replace("Z", "+00:00"))).total_seconds()
            except ValueError:
                pass
        for model, m in c.get("by_model", {}).items():
            bm = tot["by_model"].setdefault(model, {"usd": 0.0, "input": 0, "output": 0,
                                                     "cache_read": 0, "cache_write": 0, "calls": 0})
            for k in ["usd", "input", "output", "cache_read", "cache_write", "calls"]:
                bm[k] += m.get(k, 0)
            for k in ["usd", "input", "output", "cache_read", "cache_write"]:
                tot[k] += m.get(k, 0)
            tot["turns"] += m.get("calls", 0)
    return tot


def cmd_status(a):
    slug = active_slug(required=not a.quiet)
    if not slug:
        print("Активного проекта нет. Скажите агенту, какую задачу решаем, — он создаст проект.")
        return
    st = load_state(slug)
    claims = read_jsonl(pdir(slug) / "claims.jsonl")
    sources = read_jsonl(pdir(slug) / "sources.jsonl")
    decisions = read_jsonl(pdir(slug) / "decisions.jsonl")
    by_type: dict[str, int] = {}
    for c in claims:
        if c["status"] not in ("retracted", "superseded"):
            by_type[c["type"]] = by_type.get(c["type"], 0) + 1
    review = [c["id"] for c in claims if c["status"] == "needs_review"]
    cost = cost_totals(slug)
    print(f"# Проект: {st['title']} ({slug})")
    mode = st.get("mode", "full")
    print(f"Этап: {STAGE_NAMES.get(st['stage'], st['stage'])} | Режим: {'экспресс' if mode == 'express' else 'полный'}")
    print(f"Путь: {progress_bar(st)}")
    need = [g for g in MODES[mode][1] if g not in st.get("gates", {})]
    if st["stage"] in need:
        print(f"🚦 Развилка этапа: нужно подтверждение человека ({GATE_TEXT[st['stage']]}) → ./strateg gate {st['stage']} --choice \"…\"")
    q = st["brief"].get("question")
    if q:
        print(f"Вопрос: {q}")
    if st["brief"].get("decision_to_make"):
        print(f"Решение, которое надо принять: {st['brief']['decision_to_make']}")
    print(f"Источников: {len(sources)} | Утверждений: " +
          (", ".join(f"{TYPE_RU[t]} {n}" for t, n in sorted(by_type.items())) or "0") +
          f" | Решений: {len(decisions)}")
    if review:
        print(f"⚠️ Требуют пересмотра: {', '.join(review)}")
    if st.get("open_questions"):
        print("Открытые вопросы к человеку:")
        for oq in st["open_questions"]:
            print(f"  - {oq}")
    if st.get("human_decisions"):
        print("Ответы человека на развилках (учитывай, не переспрашивай):")
        for h in st["human_decisions"][-6:]:
            print(f"  - {h['question']} → {h['answer']}")
    plan = [q for q in read_jsonl(pdir(slug) / "plan.jsonl") if q["status"] in ("open", "in_progress")]
    if plan:
        print("План исследования — не закрыто:")
        for q in plan[:8]:
            mark = "▶️ начат, проверь частичный результат" if q["status"] == "in_progress" else "⬜"
            print(f"  - {q['id']} {mark}: {q['question']}")
    print(f"Следующий шаг: {st.get('next_action') or '—'}")
    if st.get("checkpoints"):
        last = st["checkpoints"][-1]
        print(f"Последняя контрольная точка: {last['at']} — {last['note']}")
    if cost["by_model"]:
        budget = st.get("budget_usd")
        share = f" — {100 * cost['usd'] / budget:.0f}% бюджета ${budget:g}" if budget else ""
        print(f"Стоимость (эквивалент API): ${cost['usd']:.2f} за {cost['sessions']} сесс., "
              f"{cost['turns']} вызовов модели{share}")
        if budget and cost["usd"] >= 0.8 * budget:
            print("⚠️ Бюджет почти исчерпан: сообщи пользователю и спроси, продолжать ли / поднять лимит (./strateg budget --set N)")
    if not a.brief:
        log = (pdir(slug) / "journal.md").read_text(encoding="utf-8").strip().splitlines()
        tail = [l for l in log if l.startswith("- ")][-a.tail:]
        if tail:
            print("\nПоследние события журнала:")
            print("\n".join(tail))


def cmd_stage(a):
    st = load_state()
    if a.name not in STAGE_KEYS:
        die(f"этап: {', '.join(STAGE_KEYS)}")
    mode = st.get("mode", "full")
    if a.status == "done" and a.name in MODES[mode][1] and a.name not in st.get("gates", {}):
        die(f"этап «{STAGE_NAMES[a.name]}» — развилка: нужно подтверждение человека ({GATE_TEXT[a.name]}). "
            f"Покажи результат пользователю, получи его выбор и выполни: ./strateg gate {a.name} --choice \"<что выбрал человек>\"")
    st["stages"][a.name] = a.status
    if a.status == "in_progress":
        st["stage"] = a.name
    if a.status == "done":
        idx = STAGE_KEYS.index(a.name)
        if idx + 1 < len(STAGE_KEYS) and st["stages"][STAGE_KEYS[idx + 1]] == "pending":
            st["stage"] = STAGE_KEYS[idx + 1]
            st["stages"][STAGE_KEYS[idx + 1]] = "in_progress"
    save_state(st)
    journal(f"Этап «{STAGE_NAMES[a.name]}»: {a.status}" + (f". {a.note}" if a.note else ""), stage=a.name)
    print(f"{STAGE_NAMES[a.name]}: {a.status}. Текущий этап: {STAGE_NAMES[st['stage']]}")


def cmd_brief(a):
    st = load_state()
    b = st["brief"]
    for key in ["question", "goal", "recipient", "decision_to_make", "deadline", "success_criteria"]:
        v = getattr(a, key)
        if v is not None:
            b[key] = v
    for key in ["constraints", "available_data"]:
        v = getattr(a, key)
        if v:
            b[key] = b.get(key, []) + v
    save_state(st)
    journal("Обновлена постановка задачи")
    print(json.dumps(b, ensure_ascii=False, indent=2))


def cmd_next(a):
    st = load_state()
    st["next_action"] = a.text
    save_state(st)
    print(f"Следующий шаг: {a.text}")


def cmd_ask(a):
    st = load_state()
    if a.clear:
        st["open_questions"] = []
    if a.text:
        st["open_questions"].append(a.text)
    save_state(st)
    print("Открытые вопросы:", *st["open_questions"], sep="\n  - ")


def cmd_checkpoint(a):
    st = load_state()
    st["checkpoints"].append({"at": now(), "stage": st["stage"], "note": a.note})
    save_state(st)
    journal(f"Контрольная точка: {a.note}")
    print("Контрольная точка сохранена. Работу можно продолжить в новом чате командой /strateg")


def cmd_human(a):
    """Фиксирует выбор человека на развилке (чтобы агент не переспрашивал после перерыва)."""
    st = load_state()
    st["human_decisions"].append({"at": now(), "stage": st["stage"], "question": a.question, "answer": a.answer})
    save_state(st)
    journal(f"Выбор человека: {a.question} → {a.answer}")
    print("Записано")


def cmd_log(a):
    journal(a.text)
    print("Записано в журнал")


# ---------------------------------------------------------------- источники
def file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def cmd_source_add(a):
    p = pdir() / "sources.jsonl"
    rows = read_jsonl(p)
    if a.url:
        dup = [r for r in rows if r.get("url") == a.url]
        if dup:
            print(f"{dup[0]['id']} (уже зарегистрирован)")
            return
    if a.path:
        dup = [r for r in rows if r.get("path") == a.path]
        if dup:
            print(f"{dup[0]['id']} (уже зарегистрирован)")
            return
    sid = next_id(rows, "S")
    row = {
        "id": sid, "title": a.title, "kind": a.kind, "url": a.url, "path": a.path,
        "snapshot": a.snapshot, "reliability": a.reliability, "published": a.published,
        "accessed_at": now(), "note": a.note, "sha": None,
    }
    for key in ["path", "snapshot"]:
        if row[key]:
            fp = (ROOT / row[key]) if not Path(row[key]).is_absolute() else Path(row[key])
            if fp.exists() and fp.is_file():
                row["sha"] = file_sha(fp)
    append_jsonl(p, row)
    print(sid)


def cmd_source_set(a):
    """Переоценка источника. При понижении надёжности зависимые факты уходят на пересмотр."""
    p = pdir() / "sources.jsonl"
    rows = read_jsonl(p)
    sid = a.id.upper()
    src = next((r for r in rows if r["id"] == sid), None) or die(f"нет источника {sid}")
    old = src.get("reliability")
    changes = {}
    if a.reliability:
        changes["reliability"] = a.reliability
    for key in ["title", "url", "snapshot", "published", "note"]:
        v = getattr(a, key)
        if v is not None:
            changes[key] = v
    src.update(changes)
    src.setdefault("revisions", []).append({"at": now(), "changes": changes, "old_reliability": old, "reason": a.reason})
    write_jsonl(p, rows)
    history({"event": "source_set", "id": sid, "changes": changes, "reason": a.reason})
    msg = f"{sid} обновлён: {', '.join(changes) or 'нет изменений'}"
    downgraded = a.reliability and old and a.reliability > old  # A < B < … < E
    if downgraded:
        claims = load_claims()
        decisions = read_jsonl(pdir() / "decisions.jsonl")
        order, decs = impacted([sid], claims, decisions)
        affected = set(order)
        marked = []
        for c in claims:
            if c["id"] in affected and c["status"] in ("active", "confirmed"):
                c["status"] = "needs_review"
                c["updated_at"] = now()
                c.setdefault("revisions", []).append({"at": now(), "status": "needs_review",
                                                      "reason": f"надёжность {sid} понижена {old}→{a.reliability}: {a.reason}"})
                marked.append(c["id"])
        save_claims(claims)
        for d in decisions:
            if d["id"] in decs and d.get("status") == "active":
                d["status"] = "needs_review"
                d.setdefault("revisions", []).append({"at": now(), "reason": f"надёжность {sid} понижена"})
        write_jsonl(pdir() / "decisions.jsonl", decisions)
        msg += f". Надёжность {old}→{a.reliability}; на пересмотр: {', '.join(marked + decs) or '—'}"
    journal(f"Источник {msg} ({a.reason})")
    print(msg)


def cmd_source_list(a):
    for r in read_jsonl(pdir() / "sources.jsonl"):
        loc = r.get("url") or r.get("path") or ""
        rev = " (переоценён)" if r.get("revisions") else ""
        print(f"{r['id']} [{r['reliability']}]{rev} {r['kind']:8} {r['title']} — {loc}")


# ---------------------------------------------------------------- утверждения
def load_claims() -> list[dict]:
    return read_jsonl(pdir() / "claims.jsonl")


def save_claims(rows: list[dict]):
    write_jsonl(pdir() / "claims.jsonl", rows)


def history(event: dict):
    event["at"] = now()
    append_jsonl(pdir() / "history.jsonl", event)


def cmd_claim_add(a):
    rows = load_claims()
    sources = {s["id"] for s in read_jsonl(pdir() / "sources.jsonl")}
    ids = {c["id"] for c in rows}
    src = split_ids(a.source)
    dep = split_ids(a.depends)
    bad_src = [s for s in src if s not in sources]
    bad_dep = [d for d in dep if d not in ids]
    if bad_src:
        die(f"нет таких источников: {', '.join(bad_src)}. Сначала: strateg source add")
    if bad_dep:
        die(f"нет таких утверждений: {', '.join(bad_dep)}")
    if a.type == "fact" and not src:
        die("факт без источника недопустим. Укажите --source или запишите как hypothesis/assumption")
    if a.type in ("conclusion", "recommendation") and not dep:
        die("вывод/рекомендация должны опираться на утверждения: укажите --depends")
    if a.type == "synthetic" and not src:
        die("синтетика должна ссылаться на источник-расшифровку беседы (kind=synthetic)")
    cid = next_id(rows, CLAIM_TYPES[a.type])
    row = {
        "id": cid, "type": a.type, "text": a.text, "sources": src, "depends_on": dep,
        "confidence": a.confidence, "status": "active", "stage": load_state()["stage"],
        "evidence": a.evidence, "created_at": now(), "updated_at": now(), "note": a.note,
    }
    rows.append(row)
    save_claims(rows)
    history({"event": "claim_added", "id": cid, "type": a.type})
    print(cid)


def cmd_claim_list(a):
    rows = load_claims()
    for c in rows:
        if a.type and c["type"] != a.type:
            continue
        if a.status and c["status"] != a.status:
            continue
        if not a.all and c["status"] in ("retracted", "superseded") and not a.status:
            continue
        refs = ",".join(c["sources"]) or "-"
        dep = ",".join(c["depends_on"]) or "-"
        flag = "" if c["status"] == "active" else f" [{c['status']}]"
        print(f"{c['id']}{flag} ({TYPE_RU[c['type']]}, {c['confidence']}) {c['text']}  ⟵ src:{refs} dep:{dep}")


def dependents_map(rows: list[dict]) -> dict[str, set[str]]:
    m: dict[str, set[str]] = {}
    for c in rows:
        for d in c["depends_on"]:
            m.setdefault(d, set()).add(c["id"])
    return m


def impacted(root_ids: list[str], rows: list[dict], decisions: list[dict]) -> tuple[list[str], list[str]]:
    """Все утверждения и решения, транзитивно опирающиеся на root_ids (источники или утверждения)."""
    dmap = dependents_map(rows)
    frontier = set()
    for rid in root_ids:
        if rid.startswith("S-"):
            frontier |= {c["id"] for c in rows if rid in c["sources"]}
        else:
            frontier.add(rid)
    seen: set[str] = set()
    order: list[str] = []
    stack = list(frontier)
    while stack:
        x = stack.pop(0)
        if x in seen:
            continue
        seen.add(x)
        order.append(x)
        stack.extend(sorted(dmap.get(x, set())))
    affected_decisions = [d["id"] for d in decisions if set(d.get("based_on", [])) & seen]
    return order, affected_decisions


def cmd_impact(a):
    rows = load_claims()
    decisions = read_jsonl(pdir() / "decisions.jsonl")
    targets = split_ids(a.ids)
    order, decs = impacted(targets, rows, decisions)
    byid = {c["id"]: c for c in rows}
    print(f"Затронуто утверждений: {len(order)}; решений: {len(decs)}")
    for cid in order:
        c = byid.get(cid)
        if c:
            print(f"  {cid} ({TYPE_RU[c['type']]}) {c['text']}")
    for did in decs:
        print(f"  {did} (решение)")


def cmd_revise(a):
    """Меняет статус утверждения/источника и помечает всё зависимое как needs_review."""
    rows = load_claims()
    decisions = read_jsonl(pdir() / "decisions.jsonl")
    targets = split_ids(a.ids)
    byid = {c["id"]: c for c in rows}
    for t in targets:
        if t.startswith("S-"):
            continue
        if t not in byid:
            die(f"нет утверждения {t}")
        byid[t]["status"] = a.status
        byid[t]["updated_at"] = now()
        byid[t].setdefault("revisions", []).append({"at": now(), "status": a.status, "reason": a.reason,
                                                    "new_source": split_ids(a.source)})
        if a.replacement:
            byid[t]["superseded_by"] = a.replacement
    order, decs = impacted(targets, rows, decisions)
    marked = []
    for cid in order:
        if cid in targets or cid not in byid:
            continue
        c = byid[cid]
        if c["status"] in ("active", "confirmed"):
            c["status"] = "needs_review"
            c["updated_at"] = now()
            c.setdefault("revisions", []).append({"at": now(), "status": "needs_review",
                                                  "reason": f"основание изменилось: {', '.join(targets)}"})
            marked.append(cid)
    for d in decisions:
        if d["id"] in decs and d.get("status") == "active":
            d["status"] = "needs_review"
            d.setdefault("revisions", []).append({"at": now(), "reason": f"основание изменилось: {', '.join(targets)}"})
    save_claims(rows)
    write_jsonl(pdir() / "decisions.jsonl", decisions)
    history({"event": "revise", "targets": targets, "status": a.status, "reason": a.reason,
             "marked": marked, "decisions": decs})
    journal(f"Пересмотр: {', '.join(targets)} → {a.status} ({a.reason}). "
            f"На пересмотр: {', '.join(marked + decs) or 'ничего'}")
    print(f"{', '.join(targets)} → {a.status}")
    print(f"Помечены на пересмотр: {', '.join(marked) or '—'}")
    print(f"Решения на пересмотр: {', '.join(decs) or '—'}")


def cmd_claim_set(a):
    rows = load_claims()
    byid = {c["id"]: c for c in rows}
    c = byid.get(a.id.upper()) or die(f"нет утверждения {a.id}")
    changes = {}
    if a.status:
        changes["status"] = a.status
    if a.confidence:
        changes["confidence"] = a.confidence
    if a.text:
        changes["text"] = a.text
    if a.add_source:
        changes["sources"] = sorted(set(c["sources"]) | set(split_ids(a.add_source)))
    c.update(changes)
    c["updated_at"] = now()
    c.setdefault("revisions", []).append({"at": now(), "changes": list(changes), "reason": a.reason})
    save_claims(rows)
    history({"event": "claim_set", "id": c["id"], "changes": changes, "reason": a.reason})
    print(f"{c['id']} обновлено: {', '.join(changes) or 'нет изменений'}")


# ---------------------------------------------------------------- решения
def cmd_decision_add(a):
    p = pdir() / "decisions.jsonl"
    rows = read_jsonl(p)
    did = next_id(rows, "D")
    row = {
        "id": did, "title": a.title, "chosen_option": a.option, "owner": a.owner,
        "metrics": a.metric or [], "review_by": a.review_by, "review_if": a.review_if or [],
        "commitments": a.commitment or [], "based_on": split_ids(a.based_on),
        "rejected": a.rejected or [], "status": "active", "created_at": now(),
    }
    append_jsonl(p, row)
    journal(f"Решение {did}: {a.title} → {a.option} (ответственный: {a.owner})")
    print(did)


def cmd_decision_list(a):
    rows = read_jsonl(pdir() / "decisions.jsonl")
    claims = {c["id"]: c for c in load_claims()}
    for d in rows:
        print(f"{d['id']} [{d['status']}] {d['title']} → {d['chosen_option']} | отв.: {d['owner']} | "
              f"пересмотр до: {d.get('review_by') or '—'}")
        for m in d.get("metrics", []):
            print(f"    метрика: {m}")
        for c in d.get("commitments", []):
            print(f"    обязательство: {c}")
        weak = [b for b in d["based_on"] if claims.get(b, {}).get("status") not in ("active", "confirmed")]
        if weak:
            print(f"    ⚠️ ослабли основания: {', '.join(weak)}")


def cmd_decision_due(a):
    """Что пора решать/пересматривать сейчас."""
    rows = read_jsonl(pdir() / "decisions.jsonl")
    claims = {c["id"]: c for c in load_claims()}
    t = a.date or today()
    found = False
    for d in rows:
        reasons = []
        if d.get("review_by") and d["review_by"] <= t:
            reasons.append(f"наступил срок пересмотра {d['review_by']}")
        weak = [b for b in d["based_on"] if claims.get(b, {}).get("status") not in ("active", "confirmed")]
        if weak:
            reasons.append(f"основания изменились: {', '.join(weak)}")
        if d.get("status") == "needs_review":
            reasons.append("помечено на пересмотр")
        if reasons:
            found = True
            print(f"{d['id']} {d['title']} — " + "; ".join(reasons))
            for cond in d.get("review_if", []):
                print(f"    условие пересмотра: {cond}")
    if not found:
        print("Сейчас пересматривать нечего")


def cmd_decision_set(a):
    p = pdir() / "decisions.jsonl"
    rows = read_jsonl(p)
    for d in rows:
        if d["id"] == a.id.upper():
            d["status"] = a.status
            d.setdefault("revisions", []).append({"at": now(), "status": a.status, "reason": a.reason})
            write_jsonl(p, rows)
            journal(f"Решение {d['id']} → {a.status}: {a.reason}")
            print("Обновлено")
            return
    die(f"нет решения {a.id}")


# ---------------------------------------------------------------- проверки
def cmd_check(a):
    """Линтер доказательности: ловит то, за что жюри снимет баллы."""
    slug = active_slug()
    claims = load_claims()
    sources = {s["id"]: s for s in read_jsonl(pdir() / "sources.jsonl")}
    byid = {c["id"]: c for c in claims}
    errors, warns = [], []
    for c in claims:
        if c["status"] in ("retracted", "superseded"):
            continue
        if c["type"] == "fact" and not c["sources"]:
            errors.append(f"{c['id']}: факт без источника")
        if c["type"] in ("conclusion", "recommendation") and not c["depends_on"]:
            errors.append(f"{c['id']}: вывод без оснований")
        for s in c["sources"]:
            if s not in sources:
                errors.append(f"{c['id']}: ссылка на несуществующий источник {s}")
            elif sources[s]["reliability"] in ("D", "E") and c["type"] == "fact" and c["confidence"] == "high":
                warns.append(f"{c['id']}: высокая уверенность при слабом источнике {s} ({sources[s]['reliability']})")
        for d in c["depends_on"]:
            dep = byid.get(d)
            if not dep:
                errors.append(f"{c['id']}: опирается на несуществующее {d}")
            elif dep["status"] in ("retracted", "superseded", "needs_review", "contested") and c["status"] == "active":
                warns.append(f"{c['id']}: опирается на {d} в статусе {dep['status']}")
            elif dep["type"] == "synthetic" and c["type"] == "fact":
                errors.append(f"{c['id']}: факт не может опираться на синтетику {d}")
        if c["status"] == "needs_review":
            warns.append(f"{c['id']}: требует пересмотра")
    synth_only = [c["id"] for c in claims if c["type"] in ("conclusion", "recommendation") and c["depends_on"]
                  and all(byid.get(d, {}).get("type") == "synthetic" for d in c["depends_on"])]
    for s in synth_only:
        errors.append(f"{s}: вывод опирается только на синтетических респондентов")
    # проверка ссылок в итоговых документах
    out = pdir() / "outputs"
    for md in sorted(out.glob("*.md")):
        text = md.read_text(encoding="utf-8")
        for m in ID_RE.finditer(text):
            rid = f"{m.group(1)}-{m.group(2)}"
            if rid.startswith("S-"):
                if rid not in sources:
                    errors.append(f"{md.name}: ссылка на несуществующий источник {rid}")
            elif rid.startswith("D-"):
                continue
            elif rid not in byid:
                errors.append(f"{md.name}: ссылка на несуществующее утверждение {rid}")
            elif byid[rid]["status"] in ("retracted", "superseded"):
                errors.append(f"{md.name}: используется отозванное утверждение {rid}")
            elif byid[rid]["status"] == "needs_review":
                warns.append(f"{md.name}: используется утверждение на пересмотре {rid}")
    opts = out / "03_options.md"
    if opts.exists():
        t = opts.read_text(encoding="utf-8").lower()
        if "оставить как есть" not in t and "статус-кво" not in t:
            errors.append("03_options.md: нет варианта «оставить как есть»")
        if "закрыть" not in t:
            warns.append("03_options.md: не рассмотрен вариант «закрыть направление»")
        if t.count("возражени") < 3:
            warns.append("03_options.md: у вариантов мало сильных возражений")
    if not any(c["type"] == "gap" for c in claims):
        warns.append("не зафиксировано ни одного пробела (gap) — честно назовите, чего мы не знаем")
    import strateg_ext
    strateg_ext.extra_checks(claims, sources, errors, warns)
    print(f"Проверка проекта {slug}: ошибок {len(errors)}, предупреждений {len(warns)}")
    for e in errors:
        print(f"  ❌ {e}")
    for w in warns:
        print(f"  ⚠️ {w}")
    if errors and a.strict:
        sys.exit(2)


# ---------------------------------------------------------------- стоимость
def cmd_cost(a):
    tot = cost_totals()
    if not tot["by_model"]:
        print("Данных о стоимости пока нет (они пишутся хуком после каждого ответа агента)")
        return
    print(f"Сессий: {tot['sessions']}, вызовов модели: {tot['turns']}")
    print(f"Токены: вход {tot['input']:,}, выход {tot['output']:,}, кэш-чтение {tot['cache_read']:,}, "
          f"кэш-запись {tot['cache_write']:,}".replace(",", " "))
    print(f"Стоимость по тарифам API: ${tot['usd']:.2f}")
    print("По моделям:")
    for m, v in sorted(tot["by_model"].items(), key=lambda x: -x[1]["usd"]):
        print(f"  {m:28} ${v['usd']:.3f}  вызовов {v['calls']}")
    if tot["by_stage"]:
        print("По этапам (приблизительно — по этапу на момент ответа):")
        for k in STAGE_KEYS:
            if k in tot["by_stage"]:
                print(f"  {STAGE_NAMES[k]:28} ${tot['by_stage'][k]:.2f}")
    if tot["seconds"]:
        print(f"Время от первого до последнего действия (сумма сессий, включая ожидание человека): {tot['seconds'] / 60:.0f} мин")
    st = load_state()
    if st.get("budget_usd"):
        print(f"Бюджет: ${st['budget_usd']:g}, израсходовано {100 * tot['usd'] / st['budget_usd']:.0f}%")
    print("Примечание: на подписке Claude Pro/Max фактический платёж фиксирован; "
          "здесь — эквивалент по публичным ценам API, чтобы сравнивать варианты.")


# ---------------------------------------------------------------- отчёт
def cmd_report(a):
    slug = active_slug()
    st = load_state()
    out = pdir() / "outputs"
    parts = sorted(p for p in out.glob("[0-9][0-9]_*.md"))
    claims = load_claims()
    sources = read_jsonl(pdir() / "sources.jsonl")
    decisions = read_jsonl(pdir() / "decisions.jsonl")
    tot = cost_totals()
    lines = [f"# {st['title']}", "", f"_Отчёт собран {now()} агентом «Стратег». Проект `{slug}`._", ""]
    lines += ["> **Как читать.** В тексте стоят ссылки на реестр: **F** — факт (есть источник), **H** — гипотеза, "
              "**P** — прогноз, **A** — допущение, **Y** — синтетика (модельный респондент, не доказательство), "
              "**G** — пробел в знаниях, **K** — вывод, **R** — рекомендация, **S** — источник, **D** — решение. "
              "Полный реестр — в приложении.", ""]
    for p in parts:
        lines.append(p.read_text(encoding="utf-8").rstrip())
        lines += ["", "---", ""]
    lines += ["## Приложение A. Реестр утверждений", "",
              "| ID | Тип | Утверждение | Источники | Основания | Уверенность | Статус |", "|---|---|---|---|---|---|---|"]
    for c in claims:
        txt = c["text"].replace("|", "\\|")
        lines.append(f"| {c['id']} | {TYPE_RU[c['type']]} | {txt} | {', '.join(c['sources']) or '—'} | "
                     f"{', '.join(c['depends_on']) or '—'} | {c['confidence']} | {c['status']} |")
    lines += ["", "## Приложение B. Источники", "", "| ID | Надёжность | Тип | Название | Где |", "|---|---|---|---|---|"]
    for s in sources:
        loc = s.get("url") or s.get("path") or "—"
        lines.append(f"| {s['id']} | {s['reliability']} | {s['kind']} | {s['title'].replace('|', '/')} | {loc} |")
    if decisions:
        lines += ["", "## Приложение C. Решения и обязательства", ""]
        for d in decisions:
            lines.append(f"- **{d['id']} {d['title']}** → {d['chosen_option']}; ответственный: {d['owner']}; "
                         f"пересмотр: {d.get('review_by') or '—'}; основания: {', '.join(d['based_on'])}")
    lines += ["", "## Приложение D. Прозрачность: стоимость и ход работы", "",
              f"- Сессий агента: {tot['sessions']}, вызовов модели: {tot['turns']}",
              f"- Эквивалентная стоимость по ценам API: ${tot['usd']:.2f}",
              f"- Журнал действий: `projects/{slug}/journal.md`"]
    target = out / "REPORT.md"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    journal("Собран итоговый отчёт outputs/REPORT.md")
    print(f"Отчёт: {target}")


# ---------------------------------------------------------------- поиск
WORD_RE = re.compile(r"[a-zа-яё0-9]+", re.I)


def stems(text: str) -> list[str]:
    """Грубый стемминг: первые 5 символов слова — покрывает русские окончания (подписка/подписки/подписок)."""
    return [w[:5] for w in WORD_RE.findall(text.lower().replace("ё", "е")) if len(w) > 2]


def search_items(query: str, slug: str | None = None, limit: int = 15) -> list[dict]:
    """Поиск по формулировке (стемминг) + по связям (найденные утверждения тянут свои источники и зависимые)."""
    d = pdir(slug)
    items = []
    for c in read_jsonl(d / "claims.jsonl"):
        items.append({"id": c["id"], "kind": TYPE_RU[c["type"]], "status": c["status"],
                      "text": c["text"], "extra": (c.get("evidence") or "") + " " + (c.get("note") or ""),
                      "links": c["sources"] + c["depends_on"]})
    for s_ in read_jsonl(d / "sources.jsonl"):
        items.append({"id": s_["id"], "kind": f"источник {s_['reliability']}", "status": "",
                      "text": s_["title"], "extra": (s_.get("note") or "") + " " + (s_.get("url") or s_.get("path") or ""),
                      "links": []})
    for x in read_jsonl(d / "decisions.jsonl"):
        items.append({"id": x["id"], "kind": "решение", "status": x["status"], "text": f"{x['title']}: {x['chosen_option']}",
                      "extra": " ".join(x.get("metrics", []) + x.get("review_if", [])), "links": x["based_on"]})
    q = set(stems(query))
    direct = [w for w in ID_RE.findall(query.upper())]
    scored = []
    for it in items:
        words = stems(it["text"]) + stems(it["extra"])
        score = sum(2 if w in stems(it["text"]) else 1 for w in q if w in words)
        if f"{it['id']}" in {f"{a}-{b}" for a, b in direct}:
            score += 10
        if score:
            scored.append((score, it))
    scored.sort(key=lambda x: -x[0])
    hits = [it for _, it in scored[:limit]]
    hit_ids = {h["id"] for h in hits}
    dmap = dependents_map(read_jsonl(d / "claims.jsonl"))
    for h in hits:
        h["related"] = sorted((set(h["links"]) | dmap.get(h["id"], set())) - hit_ids)[:8]
    return hits


def cmd_search(a):
    hits = search_items(" ".join(a.query), limit=a.limit)
    if not hits:
        print("Ничего не найдено")
    for h in hits:
        st = f" [{h['status']}]" if h["status"] not in ("", "active") else ""
        rel = f"  ↔ связано: {', '.join(h['related'])}" if h.get("related") else ""
        print(f"{h['id']} ({h['kind']}){st} {h['text']}{rel}")


# ---------------------------------------------------------------- argparse
def main(argv=None):
    ap = argparse.ArgumentParser(prog="strateg", description="CLI агента «Стратег»")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="создать проект");
    p.add_argument("slug"); p.add_argument("--title"); p.add_argument("--question")
    p.add_argument("--mode", choices=list(MODES), default="full", help="full — полный путь; express — быстрый и дешёвый")
    p.add_argument("--budget", type=float, help="лимит стоимости в $ (эквивалент API)"); p.set_defaults(f=cmd_init)
    p = sub.add_parser("use", help="сделать проект активным"); p.add_argument("slug"); p.set_defaults(f=cmd_use)
    p = sub.add_parser("list", help="список проектов"); p.set_defaults(f=cmd_list)
    p = sub.add_parser("status", help="где мы сейчас")
    p.add_argument("--brief", action="store_true"); p.add_argument("--quiet", action="store_true")
    p.add_argument("--tail", type=int, default=8); p.set_defaults(f=cmd_status)
    p = sub.add_parser("stage", help="сменить статус этапа"); p.add_argument("name")
    p.add_argument("status", choices=["pending", "in_progress", "done", "skipped"]); p.add_argument("--note")
    p.set_defaults(f=cmd_stage)
    p = sub.add_parser("brief", help="записать постановку задачи")
    for k in ["question", "goal", "recipient", "decision_to_make", "deadline", "success_criteria"]:
        p.add_argument(f"--{k.replace('_', '-')}", dest=k)
    p.add_argument("--constraint", dest="constraints", action="append")
    p.add_argument("--data", dest="available_data", action="append"); p.set_defaults(f=cmd_brief)
    p = sub.add_parser("next", help="записать следующий шаг"); p.add_argument("text"); p.set_defaults(f=cmd_next)
    p = sub.add_parser("ask", help="открытый вопрос к человеку"); p.add_argument("text", nargs="?")
    p.add_argument("--clear", action="store_true"); p.set_defaults(f=cmd_ask)
    p = sub.add_parser("checkpoint", help="контрольная точка"); p.add_argument("note"); p.set_defaults(f=cmd_checkpoint)
    p = sub.add_parser("human", help="зафиксировать выбор человека")
    p.add_argument("question"); p.add_argument("answer"); p.set_defaults(f=cmd_human)
    p = sub.add_parser("log", help="запись в журнал"); p.add_argument("text"); p.set_defaults(f=cmd_log)

    s = sub.add_parser("source", help="источники").add_subparsers(dest="sc", required=True)
    p = s.add_parser("add"); p.add_argument("--title", required=True)
    p.add_argument("--kind", default="web", choices=["web", "doc", "table", "data", "interview", "synthetic",
                                                       "image", "user", "other"])
    p.add_argument("--url"); p.add_argument("--path"); p.add_argument("--snapshot")
    p.add_argument("--reliability", default="C", choices=list(RELIABILITY))
    p.add_argument("--published"); p.add_argument("--note"); p.set_defaults(f=cmd_source_add)
    p = s.add_parser("list"); p.set_defaults(f=cmd_source_list)
    p = s.add_parser("set", help="переоценить/исправить источник"); p.add_argument("id")
    p.add_argument("--reliability", choices=list(RELIABILITY)); p.add_argument("--title"); p.add_argument("--url")
    p.add_argument("--snapshot"); p.add_argument("--published"); p.add_argument("--note")
    p.add_argument("--reason", required=True); p.set_defaults(f=cmd_source_set)

    c = sub.add_parser("claim", help="реестр утверждений").add_subparsers(dest="cc", required=True)
    p = c.add_parser("add"); p.add_argument("--type", required=True, choices=list(CLAIM_TYPES))
    p.add_argument("--text", required=True); p.add_argument("--source", action="append")
    p.add_argument("--depends", action="append"); p.add_argument("--evidence", help="цитата/место в источнике")
    p.add_argument("--confidence", default="medium", choices=["high", "medium", "low"])
    p.add_argument("--note"); p.set_defaults(f=cmd_claim_add)
    p = c.add_parser("list"); p.add_argument("--type", choices=list(CLAIM_TYPES))
    p.add_argument("--status", choices=CLAIM_STATUSES); p.add_argument("--all", action="store_true")
    p.set_defaults(f=cmd_claim_list)
    p = c.add_parser("set"); p.add_argument("id"); p.add_argument("--status", choices=CLAIM_STATUSES)
    p.add_argument("--confidence", choices=["high", "medium", "low"]); p.add_argument("--text")
    p.add_argument("--add-source", action="append"); p.add_argument("--reason", required=True)
    p.set_defaults(f=cmd_claim_set)

    p = sub.add_parser("impact", help="что зависит от утверждения/источника"); p.add_argument("ids", nargs="+")
    p.set_defaults(f=cmd_impact)
    p = sub.add_parser("revise", help="пересмотр при новых данных"); p.add_argument("ids", nargs="+")
    p.add_argument("--status", default="contested", choices=["contested", "retracted", "superseded", "needs_review"])
    p.add_argument("--reason", required=True); p.add_argument("--source", action="append")
    p.add_argument("--replacement"); p.set_defaults(f=cmd_revise)

    d = sub.add_parser("decision", help="решения и обязательства").add_subparsers(dest="dc", required=True)
    p = d.add_parser("add"); p.add_argument("--title", required=True); p.add_argument("--option", required=True)
    p.add_argument("--owner", required=True); p.add_argument("--metric", action="append")
    p.add_argument("--review-by"); p.add_argument("--review-if", action="append")
    p.add_argument("--commitment", action="append"); p.add_argument("--rejected", action="append")
    p.add_argument("--based-on", action="append", required=True); p.set_defaults(f=cmd_decision_add)
    p = d.add_parser("list"); p.set_defaults(f=cmd_decision_list)
    p = d.add_parser("due"); p.add_argument("--date"); p.set_defaults(f=cmd_decision_due)
    p = d.add_parser("set"); p.add_argument("id"); p.add_argument("--status", required=True,
                                                                  choices=["active", "needs_review", "closed", "reversed"])
    p.add_argument("--reason", required=True); p.set_defaults(f=cmd_decision_set)

    p = sub.add_parser("check", help="проверка доказательности"); p.add_argument("--strict", action="store_true")
    p.set_defaults(f=cmd_check)
    p = sub.add_parser("cost", help="стоимость и токены"); p.set_defaults(f=cmd_cost)
    p = sub.add_parser("report", help="собрать итоговый отчёт"); p.set_defaults(f=cmd_report)
    p = sub.add_parser("search", help="поиск по реестру, источникам и решениям (с учётом связей)")
    p.add_argument("query", nargs="+"); p.add_argument("--limit", type=int, default=15); p.set_defaults(f=cmd_search)

    import strateg_ext
    strateg_ext.register(sub)

    a = ap.parse_args(argv)
    a.f(a)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # корректный вывод кириллицы в Windows
    main()
