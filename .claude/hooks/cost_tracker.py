#!/usr/bin/env python3
"""Stop / SubagentStop: считает токены и стоимость сессии по транскрипту Claude Code.

Идемпотентно: каждый раз пересчитывает сессию целиком и перезаписывает cost/<session>.json,
поэтому повторный запуск не задваивает расходы. Учитываются и транскрипты субагентов.
Цены — публичные цены API за 1 млн токенов (ноябрь 2026), см. docs/COSTS.md.
"""
import datetime as dt
import json
from pathlib import Path

from _common import PROJECTS, read_input, active_project_dir

# (input, output, cache_write_5m, cache_read) $ за 1M токенов; ищется по подстроке id модели
PRICES = [
    ("fable-5-2", (8.0, 40.0, 10.0, 0.20)),
    ("fable", (10.0, 50.0, 12.5, 1.0)),
    ("opus-5-5", (4.0, 20.0, 5.0, 0.20)),
    ("opus", (5.0, 25.0, 6.25, 0.50)),
    ("sonnet-5-5", (2.0, 10.0, 2.5, 0.10)),
    ("sonnet-5", (2.0, 10.0, 2.5, 0.20)),
    ("sonnet", (3.0, 15.0, 3.75, 0.30)),
    ("haiku-5-5", (0.10, 0.50, 0.125, 0.01)),
    ("haiku", (1.0, 5.0, 1.25, 0.10)),
]
DEFAULT = (2.0, 10.0, 2.5, 0.20)


def price(model: str):
    for key, p in PRICES:
        if key in model:
            return p
    return DEFAULT


def parse(files):
    seen = set()
    by_model = {}
    first = last = None
    for fp in files:
        try:
            lines = Path(fp).read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            continue
        for line in lines:
            try:
                e = json.loads(line)
            except Exception:
                continue
            ts = e.get("timestamp")
            if ts:
                first = ts if first is None or ts < first else first
                last = ts if last is None or ts > last else last
            msg = e.get("message") if isinstance(e.get("message"), dict) else None
            if not msg or e.get("type") != "assistant" or "usage" not in msg:
                continue
            key = (msg.get("id"), e.get("requestId"))
            if key in seen:  # потоковые куски одного ответа повторяют usage
                continue
            seen.add(key)
            model = msg.get("model") or "unknown"
            if model == "<synthetic>":
                continue
            u = msg["usage"]
            pi, po, pcw, pcr = price(model)
            inp, out = u.get("input_tokens", 0) or 0, u.get("output_tokens", 0) or 0
            cw, cr = u.get("cache_creation_input_tokens", 0) or 0, u.get("cache_read_input_tokens", 0) or 0
            m = by_model.setdefault(model, {"usd": 0.0, "input": 0, "output": 0, "cache_read": 0,
                                            "cache_write": 0, "calls": 0})
            m["input"] += inp; m["output"] += out; m["cache_write"] += cw; m["cache_read"] += cr
            m["calls"] += 1
            m["usd"] += (inp * pi + out * po + cw * pcw + cr * pcr) / 1_000_000
    return by_model, first, last


data = read_input()
tp = data.get("transcript_path")
sid = data.get("session_id") or "unknown"
if tp:
    main = Path(tp)
    files = [main]
    sub = main.with_suffix("") / "subagents"
    if sub.is_dir():
        files += sorted(sub.glob("*.jsonl"))
    if data.get("agent_transcript_path"):
        files.append(Path(data["agent_transcript_path"]))
    files = list(dict.fromkeys(files))
    by_model, first, last = parse(files)
    pd = active_project_dir()
    target_dir = (pd / "cost") if pd else (PROJECTS / "_unassigned_cost")
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        total = round(sum(m["usd"] for m in by_model.values()), 6)
        prev = {}
        try:
            prev = json.loads((target_dir / f"{sid}.json").read_text(encoding="utf-8"))
        except Exception:
            pass
        by_stage = dict(prev.get("by_stage", {}))
        stage = "unknown"
        if pd:
            try:
                stage = json.loads((pd / "state.json").read_text(encoding="utf-8")).get("stage", "unknown")
            except Exception:
                pass
        delta = total - sum(by_stage.values())
        if delta > 0:  # прирост с прошлого ответа относим к текущему этапу
            by_stage[stage] = round(by_stage.get(stage, 0.0) + delta, 6)
        out = {"session_id": sid, "by_stage": by_stage, "updated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
               "first_at": first, "last_at": last, "by_model": by_model,
               "usd": round(sum(m["usd"] for m in by_model.values()), 4)}
        (target_dir / f"{sid}.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
