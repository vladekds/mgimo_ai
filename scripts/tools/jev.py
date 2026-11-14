#!/usr/bin/env python3
"""Jev (TypeSafe AI, System One) — модель-«решатель»: не пишет текст, а отвечает на типизированные вопросы
(да/нет с вероятностью, выбор из вариантов, оценка по шкале). Используется как дешёвая первая ступень
маршрутизации: массовая классификация и оценка, а LLM получает только спорные случаи.

НЕОБЯЗАТЕЛЬНО. Без ключа TYPESAFE_API_KEY команды завершаются с кодом 3 и подсказкой запасного пути.

Формат запроса и ответа взят из открытых примеров (github.com/aarora79/jev-samples, README):
  POST https://api.typesafe.ai/v1/systemone   Authorization: Bearer $TYPESAFE_API_KEY
  {"model": "jev-1.13.0", "state": {...}, "questions": {"имя": {"type": "noul|choice|score", "instructions": "...",
                                                             "criteria": {...} | [...]}}}
  → {"model": ..., "answers": {"имя": {"type": "choice", "choice": "...", "confidence": 0.9, "probabilities": {...}}
                                     | {"type": "score", "score": 1.0, ...} | {"type": "noul", "noul": 0.99}},
     "usage": {"input_tokens": N, "output_tokens": M}}
ВНИМАНИЕ: с реальным ключом командой проекта не проверено. Сырой ответ сохраняется в work/jev_last_response.json,
чтобы расхождение формата было видно сразу.
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

URL = os.environ.get("JEV_API_URL", "https://api.typesafe.ai/v1/systemone")
MODEL = os.environ.get("JEV_MODEL", "jev-1.13.0")
PRICE_IN = 0.042  # $ за 1M входных токенов; выход бесплатный (по открытым обзорам цен)

CLAIM_KINDS = {
    "fact": "наблюдаемое сведение о прошлом или настоящем, которое можно проверить по источнику",
    "hypothesis": "объяснение причины или предположение, которое ещё нужно проверить",
    "forecast": "оценка или ожидание будущего значения",
    "assumption": "значение, принятое для расчёта без проверки",
}


class JevUnavailable(RuntimeError):
    pass


def ask(state: dict, questions: dict, retries: int = 3) -> dict:
    key = os.environ.get("TYPESAFE_API_KEY")
    if not key:
        raise JevUnavailable("не задан TYPESAFE_API_KEY")
    body = json.dumps({"model": MODEL, "state": state, "questions": questions}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(URL, data=body, method="POST", headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    delay = 2.0
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read().decode("utf-8"))
            if not isinstance(data.get("answers"), dict):
                raise JevUnavailable(f"неожиданный формат ответа: {str(data)[:200]}")
            return data
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 529) and attempt < retries:
                time.sleep(delay)
                delay *= 2
                continue
            raise JevUnavailable(f"HTTP {e.code}: {e.read().decode('utf-8', 'ignore')[:200]}") from e
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            if attempt < retries:
                time.sleep(delay)
                delay *= 2
                continue
            raise JevUnavailable(f"сеть/формат: {e}") from e
    raise JevUnavailable("исчерпаны попытки")


def _log_cost(S, usage_total: dict, calls: int, raw: dict):
    d = S.pdir()
    (d / "work").mkdir(exist_ok=True)
    (d / "work" / "jev_last_response.json").write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    f = d / "cost" / "jev.json"
    prev = S.read_json(f, {"kind": "external", "by_model": {}})
    m = prev["by_model"].setdefault("jev", {"usd": 0.0, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "calls": 0})
    m["input"] += usage_total.get("input_tokens", 0)
    m["output"] += usage_total.get("output_tokens", 0)
    m["calls"] += calls
    m["usd"] = round(m["input"] * PRICE_IN / 1_000_000, 6)
    S.write_json(f, prev)


def cmd_types(a, S):
    claims = S.load_claims()
    want = set(S.split_ids(a.ids)) if a.ids else None
    usage = {"input_tokens": 0, "output_tokens": 0}
    calls, mism, weak, raw = 0, [], [], {}
    for c in claims:
        if c["type"] not in CLAIM_KINDS or c["status"] in ("retracted", "superseded") or (want and c["id"] not in want):
            continue
        q = {"kind": {"type": "choice", "instructions": "К какому типу относится утверждение?", "criteria": CLAIM_KINDS}}
        state = {"claim": c["text"]}
        if c.get("evidence"):
            state["quote"] = c["evidence"]
            q["supported"] = {"type": "noul", "instructions": "Цитата из источника прямо подтверждает утверждение?"}
        raw = ask(state, q)
        calls += 1
        for k in usage:
            usage[k] += (raw.get("usage") or {}).get(k, 0)
        ans = raw["answers"]
        kind = (ans.get("kind") or {}).get("choice")
        conf = (ans.get("kind") or {}).get("confidence")
        sup = (ans.get("supported") or {}).get("noul")
        c["jev"] = {"at": S.now(), "kind": kind, "confidence": conf, "supported": sup}
        if kind and kind != c["type"]:
            mism.append((c, kind, conf))
        if sup is not None and sup < 0.5:
            weak.append((c, sup))
    S.save_claims(claims)
    _log_cost(S, usage, calls, raw)
    print(f"Jev проверил {calls} утверждений (≈${usage['input_tokens'] * PRICE_IN / 1e6:.4f}).")
    for c, kind, conf in mism:
        print(f"  ⚠️ {c['id']}: заявлен тип «{c['type']}», Jev считает «{kind}»" + (f" (уверенность {conf:.2f})" if conf else "")
              + f" — {c['text'][:80]}")
    for c, sup in weak:
        print(f"  ⚠️ {c['id']}: цитата слабо подтверждает утверждение (p={sup:.2f}) — {c['text'][:80]}")
    if mism or weak:
        print("→ Спорные id разбери сам или передай evidence-auditor; исправления — ./strateg claim set / revise.")
    else:
        print("Расхождений нет.")
    S.journal(f"Jev: проверено {calls} утверждений, расхождений типа {len(mism)}, слабых цитат {len(weak)}")


def split_options(md: str) -> dict:
    parts = re.split(r"(?m)^##\s+(Вариант[^\n]*)\n", md)
    return {parts[i].strip(): parts[i + 1].strip()[:6000] for i in range(1, len(parts) - 1, 2)}


def cmd_score(a, S):
    path = S.pdir() / "outputs" / "03_options.md"
    if not path.exists():
        S.die("нет outputs/03_options.md")
    options = split_options(path.read_text(encoding="utf-8"))
    if not options:
        S.die("в 03_options.md не найдены разделы «## Вариант …»")
    criteria = [c.strip() for c in a.criteria.split(";") if c.strip()]
    levels = ["очень слабо по этому критерию", "слабо", "средне", "хорошо", "очень хорошо"]
    usage = {"input_tokens": 0, "output_tokens": 0}
    table, raw = {}, {}
    for name, text in options.items():
        qs = {f"c{i}": {"type": "score", "instructions": f"Насколько вариант силён по критерию: {crit}?", "criteria": levels}
              for i, crit in enumerate(criteria)}
        raw = ask({"option": text}, qs)
        for k in usage:
            usage[k] += (raw.get("usage") or {}).get(k, 0)
        table[name] = [(raw["answers"].get(f"c{i}") or {}).get("score") for i in range(len(criteria))]
    _log_cost(S, usage, len(options), raw)
    out = S.pdir() / "work" / "jev_scores.json"
    out.write_text(json.dumps({"criteria": criteria, "levels": levels, "scores": table, "model": MODEL, "at": S.now()},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print("| Вариант | " + " | ".join(criteria) + " |")
    print("|---|" + "---|" * len(criteria))
    for name, row in table.items():
        print(f"| {name} | " + " | ".join("—" if v is None else f"{v:.1f}" for v in row) + " |")
    print(f"\nШкала: 0 — {levels[0]} … {len(levels) - 1} — {levels[-1]}. Сохранено: {out}")
    print("Это независимая оценка модели-«решателя», а не факт: зарегистрируй как источник kind=data, надёжность C.")
    S.journal(f"Jev: оценены {len(options)} вариантов по {len(criteria)} критериям")


def cmd_ask(a, S):
    q = {"type": a.type, "instructions": a.question}
    if a.type in ("choice", "score"):
        opts = [o.strip() for o in (a.options or "").split(";") if o.strip()]
        if len(opts) < 2:
            S.die("для choice/score нужны --options \"а;б;в\"")
        q["criteria"] = {o: o for o in opts} if a.type == "choice" else opts
    raw = ask({"text": a.state}, {"q": q})
    if S.active_slug(required=False):
        _log_cost(S, raw.get("usage") or {}, 1, raw)
    print(json.dumps(raw["answers"]["q"], ensure_ascii=False, indent=2))


def run(a, S):
    try:
        {"types": cmd_types, "score": cmd_score, "ask": cmd_ask}[a.jc](a, S)
    except JevUnavailable as e:
        print(f"Jev недоступен: {e}.\nЗапасной путь: проверку типов и цитат выполняет субагент evidence-auditor (haiku), "
              "оценку вариантов — основной агент с пометкой «экспертная оценка». Запиши: ./strateg escalate \"…\" --from jev --to haiku "
              "--reason \"Jev недоступен\"", file=sys.stderr)
        sys.exit(3)
