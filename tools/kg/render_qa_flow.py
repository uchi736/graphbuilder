# trace_p3full.json / trace_p4full.json + explain.json → qa-walkthrough.html
import json, html
S = "/tmp/claude-1000/-home-uchida/4fdbd8d5-c462-43e6-88f0-fb9cf37aba38/scratchpad/std"
T3 = {r["id"]: r for r in json.load(open(f"{S}/trace_p3full.json", encoding="utf-8"))}
T4 = {r["id"]: r for r in json.load(open(f"{S}/trace_p4full.json", encoding="utf-8"))}
EX = json.load(open(f"{S}/explain.json", encoding="utf-8"))
RUNS = [("p3", "③ リレーションに限定子（改修後）・既定モード", lambda q: T3[q]["runs"].get("graph_vector_fulltext")),
        ("p4", "④ 規定ノード・既定モード", lambda q: T4[q]["runs"].get("graph_vector_fulltext")),
        ("p4ev", "④ 規定ノード・entity_vector モード", lambda q: T4[q]["runs"].get("entity_vector"))]
def e(s): return html.escape(str(s if s is not None else "").replace("\ufffd", ""))
def cls(m): return {"○": "ok", "△": "mid", "×": "ng"}.get(m, "")
def evidence(run, keys, n=6):
    out = []
    for x in run.get("entities", []):
        s = x['id'] + (f" ({x['desc']})" if x.get("desc") and x["desc"] != "None" else "")
        if any(k in s for k in keys): out.append(("ent", s))
    seen = set()
    for x in run.get("relationships", []):
        p = f" {{{x['props']}}}" if x.get("props") else ""
        s = f"{x['s']} {x['t']}{p} {x['o']}"
        if s in seen: continue
        seen.add(s)
        if any(k in s for k in keys): out.append(("rel", s))
    ents = [o for o in out if o[0] == "ent"][: max(2, n // 2)]
    rels = [o for o in out if o[0] == "rel"][: n - len(ents)]
    return ents + rels
def run_block(title, run, ex, qid):
    if not run: return ""
    mark, why = ex.get("mark", "?"), ex.get("why", "")
    keys = ex.get("keys", [])
    chunks = "".join(
        f"<li><b>{e(c['doc'])}</b>　{e(' '.join(c['text'].split())[:120])}…"
        + (" <span class='hit'>← 答えの語あり</span>" if c.get("has_answer") else "") + "</li>"
        for c in run.get("chunks", [])[:5])
    ev = evidence(run, keys) if keys else []
    evl = "".join(f"<li class='mono'>{e(s)}</li>" for _, s in ev)
    if not ev and keys:
        evl = f"<li class='miss'>答えに関わるエンティティ・リレーションは文脈に入っていない（探した語: {e('、'.join(keys))}）</li>"
    for m in ex.get("missing", []):
        evl += f"<li class='miss mono'>× 文脈に無し: {e(m)}</li>"
    for m in ex.get("extra", []):
        evl += f"<li class='mono'>{e(m)}</li>"
    ans = " ".join(run.get("answer", "").split())
    return f"""
      <div class="run">
        <div class="run-head"><span class="mark {cls(mark)}">{e(mark)}</span><strong>{e(title)}</strong>
          <span class="meta">取り出し: チャンク {len(run.get('chunks', []))}・エンティティ {len(run.get('entities', []))}・リレーション {len(run.get('relationships', []))}</span></div>
        <div class="cols">
          <div><div class="lbl">検索で当たったチャンク（STEP 1）</div><ul class="tight chunks">{chunks or '<li>—</li>'}</ul></div>
          <div><div class="lbl">文脈に入った決め手（STEP 2–3 の結果から、答えに関わるもの）</div><ul class="tight ents">{evl or '<li>—</li>'}</ul></div>
        </div>
        <div class="lbl">生成された回答（STEP 5）</div><div class="answer">{e(ans[:460])}{'…' if len(ans) > 460 else ''}</div>
        <div class="why"><span class="mark {cls(mark)}">{e(mark)}</span><p>{e(why)}</p></div>
      </div>"""
cards, rows = [], []
for qid in sorted(T3):
    q, ex = T3[qid], EX.get(qid, {})
    body = "".join(run_block(t, get(qid), ex.get(k, {}), qid) for k, t, get in RUNS)
    point = f"<div class='point'><strong>この問いの要点　</strong>{e(ex['point'])}</div>" if ex.get("point") else ""
    cards.append(f"""
    <article class="card" id="{qid}">
      <div class="card-head"><span class="qid">{qid}</span><span class="hops">{e(q['hops'])}ホップ ／ {e(q['docs'])}</span>
        <h3>{e(q['q'])}</h3><div class="gold"><strong>正解</strong>　{e(q['gold'])}</div></div>
      {body}{point}
    </article>""")
    rows.append(f"<tr><td><a href='#{qid}'>{qid}</a></td><td>{e(q['q'][:44])}…</td>"
                + "".join(f"<td class='c {cls(ex.get(k, {}).get('mark', ''))}'>{e(ex.get(k, {}).get('mark', '?'))}</td>" for k, _, _ in RUNS) + "</tr>")
def tally(k):
    ms = [EX.get(q, {}).get(k, {}).get("mark", "") for q in T3]
    return f"○{ms.count('○')}　△{ms.count('△')}　×{ms.count('×')}"
page = open(f"{S}/qa_walkthrough_template.html", encoding="utf-8").read()
page = (page.replace("__SUMMARY_ROWS__", "".join(rows)).replace("__CARDS__", "".join(cards))
            .replace("__T3__", tally("p3")).replace("__T4__", tally("p4")).replace("__T4EV__", tally("p4ev")))
open(f"{S}/qa-walkthrough.html", "w", encoding="utf-8").write(page)
print("written", len(page), "bytes")
