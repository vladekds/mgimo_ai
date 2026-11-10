#!/usr/bin/env python3
"""Итоговый отчёт для руководителя в одном HTML-файле: ./strateg html [--slug проект]

Берёт outputs/01–07, реестр утверждений, источники, решения и стоимость активного проекта.
Ссылки [F-004], S-002, D-001 становятся кликабельными: при наведении видно утверждение, тип, уверенность и источник.
Только стандартная библиотека; файл открывается без интернета.
"""
import argparse
import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import strateg as S  # noqa: E402

TYPE_COLORS = {"F": "fact", "H": "hyp", "P": "fc", "A": "asm", "Y": "syn", "G": "gap", "K": "con", "R": "rec",
               "S": "src", "D": "dec"}
ID_RE = re.compile(r"\[?\b([FHPAYGKRSD])-(\d{3,})\b\]?")


# ------------------------------------------------------------ мини-Markdown
def inline(text: str, ctx) -> str:
    codes = []

    def keep_code(m):
        codes.append(m.group(1))
        return f"\x00{len(codes) - 1}\x00"

    text = re.sub(r"`([^`]+)`", keep_code, text)
    text = html.escape(text, quote=False)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", text)
    text = re.sub(r"(?<![\w_])_(?!\s)(.+?)(?<!\s)_(?![\w_])", r"<em>\1</em>", text)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2" target="_blank" rel="noopener">\1</a>', text)
    text = ID_RE.sub(lambda m: ctx.ref(m.group(1), m.group(2)), text)
    return re.sub(r"\x00(\d+)\x00", lambda m: f"<code>{html.escape(codes[int(m.group(1))])}</code>", text)


def md_to_html(md: str, ctx) -> str:
    lines = md.replace("\r\n", "\n").split("\n")
    out, i = [], 0
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("```"):
            buf, i = [], i + 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                buf.append(lines[i]); i += 1
            out.append(f"<pre><code>{html.escape(chr(10).join(buf))}</code></pre>"); i += 1; continue
        m = re.match(r"^(#{1,4})\s+(.*)", line)
        if m:
            lvl = len(m.group(1)) + 1  # h1 документа → h2 в отчёте
            txt = m.group(2).strip()
            anchor = ctx.anchor(txt) if lvl == 2 else ""
            out.append(f'<h{lvl}{anchor}>{inline(txt, ctx)}</h{lvl}>'); i += 1; continue
        if re.match(r"^\s*(---|\*\*\*)\s*$", line):
            out.append("<hr>"); i += 1; continue
        if line.startswith(">"):
            buf = []
            while i < len(lines) and lines[i].startswith(">"):
                buf.append(lines[i][1:].lstrip()); i += 1
            kind = "note"
            if buf and re.match(r"\[!(\w+)\]", buf[0]):
                kind = re.match(r"\[!(\w+)\]", buf[0]).group(1).lower(); buf[0] = re.sub(r"\[!\w+\]\s*", "", buf[0])
            out.append(f'<blockquote class="{kind}">{md_to_html(chr(10).join(buf), ctx)}</blockquote>'); continue
        if line.strip().startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{2,}", lines[i + 1]):
            head = [c.strip() for c in line.strip().strip("|").split("|")]
            i += 2; rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").replace("\\|", "¦").split("|")]); i += 1
            t = "<div class=tw><table><thead><tr>" + "".join(f"<th>{inline(h, ctx)}</th>" for h in head) + "</tr></thead><tbody>"
            for r in rows:
                t += "<tr>" + "".join(f"<td>{inline(c.replace('¦', '|'), ctx)}</td>" for c in r) + "</tr>"
            out.append(t + "</tbody></table></div>"); continue
        if re.match(r"^\s*([-*+]|\d+[.)])\s+", line):
            ordered = bool(re.match(r"^\s*\d+[.)]", line))
            items = []
            while i < len(lines) and (re.match(r"^\s*([-*+]|\d+[.)])\s+", lines[i]) or
                                      (lines[i].startswith("   ") and lines[i].strip() and items)):
                if re.match(r"^\s*([-*+]|\d+[.)])\s+", lines[i]):
                    items.append(re.sub(r"^\s*([-*+]|\d+[.)])\s+", "", lines[i]))
                else:
                    items[-1] += " " + lines[i].strip()
                i += 1
            tag = "ol" if ordered else "ul"
            out.append(f"<{tag}>" + "".join(f"<li>{inline(x, ctx)}</li>" for x in items) + f"</{tag}>"); continue
        if not line.strip():
            i += 1; continue
        buf = []
        while i < len(lines) and lines[i].strip() and not re.match(r"^(#{1,4}\s|>|\s*\||```|\s*([-*+]|\d+[.)])\s)", lines[i]):
            buf.append(lines[i].strip()); i += 1
        if buf:
            out.append(f"<p>{inline(' '.join(buf), ctx)}</p>")
        else:
            i += 1
    return "\n".join(out)


# ------------------------------------------------------------ контекст ссылок
class Ctx:
    def __init__(self, claims, sources, decisions):
        self.claims = {c["id"]: c for c in claims}
        self.sources = {s["id"]: s for s in sources}
        self.decisions = {d["id"]: d for d in decisions}
        self.toc = []

    def anchor(self, title):
        aid = f"sec{len(self.toc) + 1}"
        self.toc.append((aid, re.sub(r"[*`_]", "", title)))
        return f' id="{aid}"'

    def ref(self, p, n):
        rid = f"{p}-{n}"
        cls = TYPE_COLORS.get(p, "")
        tip, bad = "", ""
        if p == "S" and rid in self.sources:
            s = self.sources[rid]
            tip = f"Источник [{s['reliability']}] {s['title']}"
        elif p == "D" and rid in self.decisions:
            tip = f"Решение: {self.decisions[rid]['title']}"
        elif rid in self.claims:
            c = self.claims[rid]
            tip = f"{S.TYPE_RU[c['type']]} · уверенность {c['confidence']} · {c['status']}\n{c['text']}"
            if c["sources"]:
                tip += "\nИсточники: " + ", ".join(c["sources"])
            if c["status"] not in ("active", "confirmed"):
                bad = " stale"
        else:
            bad = " missing"
            tip = "Нет в реестре"
        return f'<a class="cid {cls}{bad}" href="#{rid}" data-tip="{html.escape(tip)}">{rid}</a>'


# ------------------------------------------------------------ сборка
CSS = """
:root{--bg:#fbfaf7;--fg:#1d1d1f;--mut:#6b6b70;--line:#e4e2dc;--card:#fff;--acc:#00985f;
--fact:#1f7a4d;--hyp:#b26a00;--fc:#6a4bc4;--asm:#7a7a7a;--syn:#c0392b;--gap:#8a6d3b;--con:#1e5aa8;--rec:#00985f;--src:#555;--dec:#0b6e75}
@media (prefers-color-scheme:dark){:root{--bg:#141416;--fg:#ececee;--mut:#9a9aa2;--line:#2c2c31;--card:#1c1c20;
--fact:#5fd39a;--hyp:#f0b45a;--fc:#a993f0;--asm:#aaa;--syn:#ff7b6b;--gap:#d6b37a;--con:#79aef5;--rec:#3ddc97;--src:#bbb;--dec:#5fc9cf}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.6 -apple-system,Segoe UI,Roboto,Inter,sans-serif}
.wrap{display:grid;grid-template-columns:260px minmax(0,1fr);max-width:1280px;margin:0 auto}
nav{position:sticky;top:0;height:100vh;overflow:auto;padding:24px 16px;border-right:1px solid var(--line);font-size:13px}
nav a{display:block;color:var(--mut);text-decoration:none;padding:4px 8px;border-radius:6px}nav a:hover{background:var(--line);color:var(--fg)}
main{padding:32px 40px;min-width:0}header{margin-bottom:24px}h1{font-size:28px;line-height:1.25;margin:0 0 8px}
h2{font-size:22px;margin:40px 0 12px;padding-top:12px;border-top:2px solid var(--line)}h3{font-size:17px;margin:24px 0 8px}h4,h5{font-size:15px}
.meta{color:var(--mut);font-size:13px}.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:18px 0}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 14px}.kpi b{display:block;font-size:20px}.kpi span{color:var(--mut);font-size:12px}
.steps{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0}.steps span{font-size:12px;padding:3px 10px;border-radius:99px;border:1px solid var(--line);color:var(--mut)}
.steps .done{background:var(--acc);border-color:var(--acc);color:#fff}.steps .in_progress{border-color:var(--acc);color:var(--acc);font-weight:600}
.tw{overflow-x:auto;margin:12px 0}table{border-collapse:collapse;width:100%;font-size:13.5px;background:var(--card)}
th,td{border:1px solid var(--line);padding:6px 9px;text-align:left;vertical-align:top}th{background:var(--line)}
blockquote{margin:14px 0;padding:10px 16px;border-left:4px solid var(--acc);background:var(--card);border-radius:0 8px 8px 0}
blockquote.warning{border-color:var(--hyp)}blockquote p{margin:4px 0}
code{background:var(--line);padding:1px 5px;border-radius:4px;font-size:12.5px}pre{background:var(--card);border:1px solid var(--line);padding:12px;overflow:auto;border-radius:8px}
a{color:var(--con)}.cid{font:600 12px/1 ui-monospace,Consolas,monospace;padding:2px 5px;border-radius:4px;text-decoration:none;border:1px solid currentColor;white-space:nowrap;position:relative}
.cid.fact{color:var(--fact)}.cid.hyp{color:var(--hyp)}.cid.fc{color:var(--fc)}.cid.asm{color:var(--asm)}.cid.syn{color:var(--syn)}.cid.gap{color:var(--gap)}
.cid.con{color:var(--con)}.cid.rec{color:var(--rec)}.cid.src{color:var(--src)}.cid.dec{color:var(--dec)}.cid.stale{text-decoration:line-through}.cid.missing{background:var(--syn);color:#fff}
#tip{position:fixed;max-width:min(420px,calc(100vw - 16px));background:var(--fg);color:var(--bg);font-size:12.5px;line-height:1.45;padding:8px 10px;border-radius:8px;white-space:pre-line;pointer-events:none;display:none;z-index:9}
.legend{display:flex;flex-wrap:wrap;gap:8px;font-size:12px;margin:8px 0}.filters button{font:inherit;font-size:12px;margin:2px;padding:3px 9px;border-radius:99px;border:1px solid var(--line);background:var(--card);color:var(--fg);cursor:pointer}
.filters button.on{background:var(--fg);color:var(--bg)}tr:target{outline:2px solid var(--acc)}
@media (max-width:860px){.wrap{display:block}nav{position:static;height:auto;border-right:0;border-bottom:1px solid var(--line)}main{padding:20px 16px}}
@media print{nav,.filters{display:none}.wrap{display:block}}
"""

JS = """
const tip=document.getElementById('tip');
document.addEventListener('mouseover',e=>{const a=e.target.closest('.cid');if(!a)return;tip.textContent=a.dataset.tip;tip.style.display='block'});
document.addEventListener('mousemove',e=>{if(tip.style.display==='block'){tip.style.left=Math.max(8,Math.min(e.clientX+14,innerWidth-tip.offsetWidth-8))+'px';tip.style.top=(e.clientY+16)+'px'}});
document.addEventListener('mouseout',e=>{if(e.target.closest('.cid'))tip.style.display='none'});
document.querySelectorAll('.filters button').forEach(b=>b.onclick=()=>{
 document.querySelectorAll('.filters button').forEach(x=>x.classList.remove('on'));b.classList.add('on');
 const t=b.dataset.t;document.querySelectorAll('#claims tbody tr').forEach(r=>r.style.display=(t==='all'||r.dataset.t===t)?'':'none')});
"""


def build(slug: str) -> Path:
    d = S.PROJECTS / slug
    st = S.read_json(d / "state.json")
    claims = S.read_jsonl(d / "claims.jsonl")
    sources = S.read_jsonl(d / "sources.jsonl")
    decisions = S.read_jsonl(d / "decisions.jsonl")
    ctx = Ctx(claims, sources, decisions)
    tot = S.cost_totals(slug)

    body = []
    for p in sorted((d / "outputs").glob("[0-9][0-9]_*.md")):
        body.append(md_to_html(p.read_text(encoding="utf-8"), ctx))

    # реестр
    rows = []
    for c in claims:
        src = " ".join(ctx.ref("S", s.split("-")[1]) for s in c["sources"])
        dep = " ".join(ctx.ref(x.split("-")[0], x.split("-")[1]) for x in c["depends_on"])
        ev = html.escape(c.get("evidence") or "")
        rows.append(f'<tr id="{c["id"]}" data-t="{c["type"]}"><td><b>{c["id"]}</b></td><td>{S.TYPE_RU[c["type"]]}</td>'
                    f'<td>{html.escape(c["text"])}{f"<div class=meta>«{ev}»</div>" if ev else ""}</td>'
                    f'<td>{src or "—"}</td><td>{dep or "—"}</td><td>{c["confidence"]}</td><td>{c["status"]}</td></tr>')
    types_present = sorted({c["type"] for c in claims}, key=list(S.CLAIM_TYPES).index)
    filters = '<div class=filters><button class=on data-t=all>все</button>' + "".join(
        f'<button data-t="{t}">{S.TYPE_RU[t]}</button>' for t in types_present) + "</div>"
    body.append(f'<h2{ctx.anchor("Реестр утверждений")}>Реестр утверждений</h2>{filters}<div class=tw><table id=claims>'
                "<thead><tr><th>ID</th><th>Тип</th><th>Утверждение и цитата</th><th>Источники</th><th>Основания</th>"
                "<th>Уверенность</th><th>Статус</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>")
    srows = []
    for s in sources:
        loc = s.get("url") or s.get("path") or ""
        link = f'<a href="{html.escape(loc)}" target="_blank" rel="noopener">{html.escape(loc[:70])}</a>' if loc.startswith("http") else html.escape(loc)
        srows.append(f'<tr id="{s["id"]}"><td><b>{s["id"]}</b></td><td>{s["reliability"]}</td><td>{s["kind"]}</td>'
                     f'<td>{html.escape(s["title"])}</td><td>{link}</td><td>{html.escape(s.get("accessed_at", "")[:10])}</td></tr>')
    body.append(f'<h2{ctx.anchor("Источники")}>Источники</h2><p class=meta>Надёжность: ' +
                "; ".join(f"<b>{k}</b> — {v}" for k, v in S.RELIABILITY.items()) +
                "</p><div class=tw><table><thead><tr><th>ID</th><th>Надёжн.</th><th>Тип</th><th>Название</th><th>Где</th><th>Дата</th></tr></thead><tbody>"
                + "".join(srows) + "</tbody></table></div>")
    if decisions:
        drows = "".join(f'<tr id="{x["id"]}"><td><b>{x["id"]}</b></td><td>{html.escape(x["title"])}</td><td>{html.escape(x["chosen_option"])}</td>'
                        f'<td>{html.escape(x["owner"])}</td><td>{"<br>".join(map(html.escape, x.get("metrics", [])))}</td>'
                        f'<td>{x.get("review_by") or "—"}<br>{"<br>".join(map(html.escape, x.get("review_if", [])))}</td>'
                        f'<td>{" ".join(ctx.ref(b.split("-")[0], b.split("-")[1]) for b in x["based_on"])}</td><td>{x["status"]}</td></tr>'
                        for x in decisions)
        body.append(f'<h2{ctx.anchor("Решения и обязательства")}>Решения и обязательства</h2><div class=tw><table><thead><tr><th>ID</th><th>Решение</th>'
                    "<th>Вариант</th><th>Ответственный</th><th>Метрики</th><th>Пересмотр</th><th>Основания</th><th>Статус</th></tr></thead><tbody>"
                    + drows + "</tbody></table></div>")
    mrows = "".join(f"<tr><td>{html.escape(m)}</td><td>{v['calls']}</td><td>{v['input'] + v['cache_read'] + v['cache_write']:,}</td>"
                    f"<td>{v['output']:,}</td><td>${v['usd']:.2f}</td></tr>".replace(",", " ")
                    for m, v in sorted(tot["by_model"].items(), key=lambda x: -x[1]["usd"]))
    body.append(f'<h2{ctx.anchor("Прозрачность: стоимость и ход работы")}>Прозрачность: стоимость и ход работы</h2>'
                f"<p>Сессий: {tot['sessions']}, обращений к моделям: {tot['turns']}, стоимость по публичным ценам API: <b>${tot['usd']:.2f}</b>. "
                "На подписке Claude фактический платёж фиксирован; цифра нужна для сравнения вариантов.</p>"
                "<div class=tw><table><thead><tr><th>Модель</th><th>Вызовов</th><th>Токены на входе</th><th>Токены на выходе</th><th>Стоимость</th></tr></thead><tbody>"
                + mrows + "</tbody></table></div>")
    journal = (d / "journal.md").read_text(encoding="utf-8") if (d / "journal.md").exists() else ""
    journal = re.sub(r"^#.*\n", "", journal)
    body.append(f'<details><summary>Журнал работы агента ({journal.count(chr(10) + "- ")} событий)</summary>{md_to_html(journal, ctx)}</details>')

    by_type = {}
    for c in claims:
        if c["status"] not in ("retracted", "superseded"):
            by_type[c["type"]] = by_type.get(c["type"], 0) + 1
    steps = "".join(f'<span class="{st["stages"].get(k, "pending")}">{n}</span>' for k, n in S.STAGES)
    kpis = [(len(sources), "источников"), (by_type.get("fact", 0), "фактов"), (by_type.get("hypothesis", 0), "гипотез"),
            (by_type.get("gap", 0), "пробелов"), (len(decisions), "решений"), (f"${tot['usd']:.2f}", "стоимость (API)")]
    legend = "".join(f'<span class="cid {TYPE_COLORS[p]}" style="cursor:default">{p}</span> {S.TYPE_RU[t]}'
                     for t, p in S.CLAIM_TYPES.items())
    nav = "".join(f'<a href="#{a}">{html.escape(t)}</a>' for a, t in ctx.toc)
    page = f"""<!doctype html><html lang=ru><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>{html.escape(st['title'])} — Стратег</title><style>{CSS}</style></head><body><div class=wrap>
<nav><b>{html.escape(st['title'])}</b><div class=meta>отчёт агента «Стратег»</div><br>{nav}</nav><main><header>
<h1>{html.escape(st['title'])}</h1><div class=meta>Вопрос: {html.escape(st['brief'].get('question') or '—')} · обновлено {S.now()[:16].replace('T', ' ')}</div>
<div class=steps>{steps}</div><div class=kpis>{''.join(f'<div class=kpi><b>{v}</b><span>{l}</span></div>' for v, l in kpis)}</div>
<div class=legend>{legend}</div><p class=meta>Наведите на метку, чтобы увидеть утверждение и источник. Зачёркнутая метка — утверждение на пересмотре или отозвано.</p>
</header>{''.join(body)}</main></div><div id=tip></div><script>{JS}</script></body></html>"""
    out = d / "outputs" / "REPORT.html"
    out.write_text(page, encoding="utf-8")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug")
    a = ap.parse_args()
    slug = a.slug or S.active_slug()
    out = build(slug)
    S.journal("Собран HTML-отчёт outputs/REPORT.html", slug)
    print(out.relative_to(ROOT))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
