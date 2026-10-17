# ③だけ・代表例だけの資料: trace_p3full.json + explain.json(p3) → qa-walkthrough-p3.html
import json, html
S = "/tmp/claude-1000/-home-uchida/4fdbd8d5-c462-43e6-88f0-fb9cf37aba38/scratchpad/std"
T3 = {r["id"]: r for r in json.load(open(f"{S}/trace_p3full.json", encoding="utf-8"))}
EX = json.load(open(f"{S}/explain.json", encoding="utf-8"))
PATTERNS = [
  ("A", "本文に答えがあり、全文検索で届く", ["M01", "M02", "M05", "M08", "M14", "M16"], "M01",
   "質問文に 2 つ目の文書の語が含まれ、STEP 1 でその文書のチャンクが直接当たる。リレーションは裏付けにとどまる。"),
  ("B", "本文に無い答えを、リレーション（プロパティ・参照）が運んだ", ["M04", "M06", "M09"], "M06",
   "上位チャンクに答えの語が無く、展開で入ったリレーションのプロパティや REFERENCES から答えた。③の改修が効いた問い。"),
  ("C", "項目ノードは届いたが、値を持つJUDGED_BY のリレーションが届かない", ["M07", "M12", "M15", "M10"], "M12",
   "対象の計測項目と DEFINED_IN・計測箇所のリレーションは文脈にあるのに、JUDGED_BY のリレーションが 20 経路の枠から外れた。"),
  ("D", "起点チャンクが外れ、答えを写したエンティティも届かない", ["M03", "M13"], "M03",
   "答えを含むチャンクが STEP 1 で当たらず、展開でもそれを名前に持つエンティティに届かなかった。"),
  ("E", "3 文書×2 事実の集約", ["M11"], "M11",
   "チャンク 5 件・1 エンティティ 20 経路の枠では、3 文書から必要なリレーションが揃わない。"),
]
PAT_OF = {q: p for p, _, qs, _, _ in PATTERNS for q in qs}
def e(s): return html.escape(str(s if s is not None else "").replace("�", ""))
def cls(m): return {"○": "ok", "△": "mid", "×": "ng"}.get(m, "")
def evidence(run, keys, n=6):
    out = []
    for x in run.get("entities", []):
        s = x['id']
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
def card(qid):
    q, ex = T3[qid], EX[qid]["p3"]
    run = q["runs"]["graph_vector_fulltext"]
    mark, why, keys = ex["mark"], ex["why"], ex.get("keys", [])
    chunks = "".join(
        f"<li><b>{e(c['doc'])}</b>　{e(' '.join(c['text'].split())[:120])}…"
        + (" <span class='hit'>← 答えの語あり</span>" if c.get("has_answer") else "") + "</li>"
        for c in run["chunks"][:5])
    ev = evidence(run, keys)
    evl = "".join(f"<li class='mono'>{e(s)}</li>" for _, s in ev)
    if not ev:
        evl = f"<li class='miss'>答えに関わるエンティティ・リレーションは文脈に入っていない（探した語: {e('、'.join(keys))}）</li>"
    for m in ex.get("missing", []):
        evl += f"<li class='miss mono'>× 文脈に無し: {e(m)}</li>"
    ans = " ".join(run["answer"].split())
    return f"""
    <article class="card" id="{qid}">
      <div class="card-head"><span class="qid">{qid}</span><span class="hops">{e(q['hops'])}ホップ ／ {e(q['docs'])}</span>
        <h3>{e(q['q'])}</h3><div class="gold"><strong>正解</strong>　{e(q['gold'])}</div></div>
      <div class="run">
        <div class="run-head"><span class="mark {cls(mark)}">{e(mark)}</span><strong>③ リレーションに限定子（改修後）・既定モード</strong>
          <span class="meta">文脈: チャンク {len(run['chunks'])}・エンティティ {len(run['entities'])}・リレーション {len(run['relationships'])}</span></div>
        <div class="cols">
          <div><div class="lbl">検索で当たったチャンク（STEP 1）</div><ul class="tight chunks">{chunks}</ul></div>
          <div><div class="lbl">文脈に入った決め手（STEP 2–3、答えに関わるもの）</div><ul class="tight ents">{evl}</ul></div>
        </div>
        <div class="lbl">生成された回答（STEP 5）</div><div class="answer">{e(ans[:460])}{'…' if len(ans) > 460 else ''}</div>
        <div class="why"><span class="mark {cls(mark)}">{e(mark)}</span><p>{e(why)}</p></div>
      </div>
    </article>"""
rows = []
for qid in sorted(T3):
    q = T3[qid]; m = EX[qid]["p3"]["mark"]; p = PAT_OF[qid]
    rows.append(f"<tr><td><a href='#P{p}'>{qid}</a></td><td>{e(q['q'][:46])}…</td><td class='c {cls(m)}'>{e(m)}</td>"
                f"<td><span class='pat'>{p}</span>　{e(dict((x[0], x[1]) for x in PATTERNS)[p])}</td></tr>")
pats = []
for p, title, qs, rep, desc in PATTERNS:
    marks = "".join(f"<span class='mark {cls(EX[x]['p3']['mark'])}' style='width:20px;height:20px;font-size:12px'>{EX[x]['p3']['mark']}</span> <span class='mono'>{x}</span>　" for x in qs)
    others = [x for x in qs if x != rep]
    other_lines = "".join(f"<li><b>{x}</b>　{e(EX[x]['p3']['why'][:150])}…</li>" for x in others)
    pats.append(f"""
    <section class="pattern" id="P{p}">
      <div class="ph"><span class="pid">パターン {p}</span><h3>{e(title)}</h3></div>
      <p class="cap">{e(desc)}　該当: {marks}</p>
      {card(rep)}
      {('<div class="lbl" style="margin-top:10px">同じパターンの他の問い（要点のみ）</div><ul class="tight" style="margin-bottom:4px">' + other_lines + '</ul>') if other_lines else ''}
    </section>""")
def tally():
    ms = [EX[q]["p3"]["mark"] for q in T3]
    return f"○{ms.count('○')}　△{ms.count('△')}　×{ms.count('×')}"
page = open(f"{S}/qa_p3_template.html", encoding="utf-8").read()
worked = open(f"{S}/fig_worked.html", encoding="utf-8").read()
page = page.replace("__ROWS__", "".join(rows)).replace("__PATTERNS__", "".join(pats)).replace("__T3__", tally()).replace("__WORKED__", worked)
page = page.replace("文脈: チャンク", "取り出し: チャンク")
open(f"{S}/qa-walkthrough-p3.html", "w", encoding="utf-8").write(page)
print("written", len(page), "chars; FFFD:", page.count("�"))
