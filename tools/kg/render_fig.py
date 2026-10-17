# M06 を例に STEP1〜5 を実データで図解し、M12 が×になる箇所を対照で示す HTML 断片 → fig_worked.html
import json, html, re
S = "/tmp/claude-1000/-home-uchida/4fdbd8d5-c462-43e6-88f0-fb9cf37aba38/scratchpad/std"
D1 = json.load(open(f"{S}/diag_steps.json", encoding="utf-8"))      # 索引ごとの生スコア、5チャンク版の起点/展開（T001 p1 のエンティティを含む）
D2 = json.load(open(f"{S}/diag_steps2.json", encoding="utf-8"))     # トレースのチャンクを起点にした起点/展開
R3 = json.load(open(f"{S}/diag_retriever_p3.json", encoding="utf-8"))  # GB 本体のリトリーバ連鎖（③グラフ）
T3 = {r["id"]: r for r in json.load(open(f"{S}/trace_p3full.json", encoding="utf-8"))}
SH = {"VTR-T_発行目録.pdf": "目録", "HS-VTR-T001E.pdf": "T001", "HS-VTR-T002F.pdf": "T002", "VTR-T003D.pdf": "T003", "VTR-T004C.pdf": "T004", "VTR-T005B.pdf": "T005"}
def e(s): return html.escape(str(s if s is not None else "").replace("�", ""))
def sh(doc): return SH.get(doc, doc or "?")
LAB = re.compile(r"(?:Standard|MeasurementItem|Criterion|Condition|Location|Instrument|Organization|Requirement):(?=\S)")
def hd(s, n): return e(LAB.sub("", " ".join(str(s).replace("�", "").split()))[:n]) + "…"

# ================= M06 =================
Q, KEY = "M06", "V00862"
m1, m2, r3, tr = D1[Q], D2[Q], R3[Q], T3[Q]["runs"]["graph_vector_fulltext"]
rows = sorted(r3["rows"], key=lambda r: -max(c["score"] for c in r["chunks"]))
kept = [(c["doc"], c["pos"], c["score"]) for r in rows for c in r["chunks"]]
vmax = max(x["score"] for x in m1["vector_top5"]); fmax = max(x["score"] for x in m1["fulltext_top"])
def rank(items, mx, n=5):
    return "".join(f"<li><span class='sc'>{x['score']/mx:.3f}</span><b>{e(sh(x['doc']))} p{x['pos']}</b><span class='hd'>{hd(x['head'],30)}</span></li>" for x in items[:n])
union = "".join(f"<li><span class='sc'>{s:.3f}</span><b>{e(sh(d))} p{p}</b><span class='hd'>{'V00862 を含む' if False else '本文に V00862 なし'}</span></li>" for d, p, s in kept)
step1 = f"""
<section class="wstep"><div class="wh"><span class="wn">STEP 1</span><span class="wt">検索：チャンクを取る</span></div>
<div class="wgrid3">
  <div class="wbox"><div class="wl">ベクトル索引（ruri、上位 5・最大値で正規化）</div><ol class="rank">{rank(m1['vector_top5'], vmax)}</ol><div class="wnote">生の類似度は 0.934〜0.943 で横一線。順位は紙一重。</div></div>
  <div class="wbox"><div class="wl">全文索引（Lucene、上位 5・最大値で正規化）</div><ol class="rank">{rank(m1['fulltext_top'], fmax)}</ol><div class="wnote">語の一致なので差がつく（生スコア 15.3〜13.1）。</div></div>
  <div class="wbox hi"><div class="wl">統合 → 残るチャンク <b>4 件</b></div><ol class="rank">{union}</ol><div class="wnote">2 つの索引を合わせ、高い順に k 件。コードは top_k=5 を渡しているが langchain 側の引数名は k なので無視され、<b>既定の 4 件</b>になる（実測でも常に 4）。0.5 未満の足切りは全部 0.99 台なので効かない。<b>答えの語 V00862 を含むチャンクは 1 件も無い</b>。</div></div>
</div></section>"""

# 起点: 各チャンクのエンティティ（T003 p3・目録 p2・T004 p1 は D2、T001 p1 は D1 から）
seeds = {}
for x in m2["seed_entities"]:
    for p in x.get("fromPos", []):
        seeds.setdefault((x["doc"], p), {})[x["id"]] = x
for x in m1["seed_entities"]:
    if x["doc"] == "HS-VTR-T001E.pdf": seeds.setdefault(("HS-VTR-T001E.pdf", 1), {})[x["id"]] = x
exp = {}
for x in m2["expansion"] + m1["expansion"]: exp.setdefault(x["id"], x)
def chip(x): return f"<span class='chip {'std' if x['label']=='Standard' else ''}' title='{e(x['label'])}'>{e(x['id'][:26])}</span>"
def seed_col(d, p):
    items = sorted(seeds.get((d, p), {}).values(), key=lambda x: -x["degree"])
    return f"<div class='wbox'><div class='wl'>{e(sh(d))} p{p}　→ エンティティ {len(items)}</div><ul class='seeds'>" + "".join(f"<li>{chip(x)}<span class='deg'>リレーション {x['degree']}</span></li>" for x in items) + "</ul></div>"
step2 = f"""
<section class="wstep"><div class="wh"><span class="wn">STEP 2</span><span class="wt">起点：当たった 4 チャンクにぶら下がるエンティティを集める</span></div>
<div class="wgrid4">{''.join(seed_col(d, p) for d, p, _ in kept)}</div>
<div class="wnote">HAS_ENTITY でつながるエンティティを「ヒットしたチャンク何件に出るか」順に、文書ごと 40 件まで。ここでは各 4〜7 件なので上限は効かない。規格ノード（青枠。VTR-T001 はリレーション 64 本、VTR-T005 は 61 本）が起点に混ざるのがポイント。</div></section>"""

# 展開
allseeds = {}
for d, p, _ in kept:
    for sid in seeds.get((d, p), {}):
        if sid in exp: allseeds[sid] = exp[sid]
def bar(x):
    deg, inc = x["degree"], x["n_included"]; cut = deg - inc
    return (f"<li>{chip(x)}<span class='barwrap'><span class='bar' style='width:{max(2, deg*100//64)}%'><span class='barin' style='width:{(inc*100//deg) if deg else 0}%'></span></span></span>"
            f"<span class='deg'>入った {inc} ／ 持っている {deg}{(' <em>' + str(cut) + ' 本は入らず</em>') if cut else ''}</span></li>")
bars = "".join(bar(x) for x in sorted(allseeds.values(), key=lambda x: -x["degree"]))
t4 = exp["VTR-T004"]; t5 = exp["VTR-T005"]
def names(items, key_id=None):
    out = []
    for i in items:
        nm = e(i["other"][:26])
        if key_id and key_id in i["other"]: nm = f"<b class='keyname'>{nm} ★答え</b>"
        out.append(nm)
    return "、".join(out)
def group(items, key_id=None):
    refs = [i for i in items if i["type"] == "REFERENCES"]
    defs = [i for i in items if i["type"] == "DEFINED_IN"]
    others = [i for i in items if i["type"] not in ("REFERENCES", "DEFINED_IN")]
    html_ = ""
    if refs: html_ += f"<li><span class='gk'>VTR-T004 が引用している規格（VTR-T004 <b>—REFERENCES→</b> 相手）{len(refs)} 本</span><div class='gv'>{names(refs, key_id)}</div></li>"
    if defs: html_ += f"<li><span class='gk'>VTR-T004 に定義されている計測項目（項目 <b>—DEFINED_IN→</b> VTR-T004）{len(defs)} 本</span><div class='gv'>{names(defs)}</div></li>"
    if others: html_ += f"<li><span class='gk'>その他 {len(others)} 本</span><div class='gv'>{names(others)}</div></li>"
    return html_
key_rel = next(i for i in t4["included"] if KEY in i["other"])
t5_ref_out = sum(1 for i in t5["excluded"] if i["type"] == "REFERENCES")
t5_in_types = {}
for i in t5["included"]: t5_in_types[i["type"]] = t5_in_types.get(i["type"], 0) + 1
step3 = f"""
<section class="wstep"><div class="wh"><span class="wn">STEP 3</span><span class="wt">展開：起点エンティティごとに、隣のエンティティを 1 段だけたどる。ただし 1 エンティティにつき Neo4j から受け取るのは最大 20 行（自分 1 行＋隣 19 行）</span></div>
<div class="wgrid2">
  <div class="wbox"><div class="wl">起点エンティティごとに、文脈に入ったリレーション数 ／ グラフ上で持っているリレーション数</div><ul class="bars">{bars}</ul>
    <div class="wnote"><b>たどった先のチャンク本文は取らない。</b>文脈に足されるのは、隣のエンティティの名前 1 行と、そこへのリレーション 1 行（プロパティ付き）だけ。Chunk・Document ノードは展開の経路から除外されている。だから本文（Text Content）は STEP 1 の 4 チャンク分のまま増えず、値を LLM に届ける経路はリレーションのプロパティかノードの説明文しかない。</div>
    <div class="wnote">緑が文脈に入った分、薄赤が入らなかった分。規格ノード（青枠）は「その規格に定義された計測項目」と「引用している他の規格」の両方をぶら下げるので本数が多く、19 本に入る割合が下がる。<b>どの 19 本かは Neo4j が返してきた順（ほぼ作成順）で、質問との関係は見ていない</b>。</div>
    <div class="wnote">補足：本来は質問との類似度が 0.9 を超えるエンティティだけ 2 段先（40 行）まで見る仕組みだが、③のグラフではエンティティに埋め込みを付けていないので判定できず、全部 1 段止まりになっている。</div></div>
  <div class="wbox hi"><div class="wl">例：規格ノード VTR-T004 の 28 本はどう分かれたか</div>
    <div class="gh ok">文脈に入った 19 本</div><ul class="groups">{group(t4["included"], KEY)}</ul>
    <div class="gh ng">入らなかった 9 本</div><ul class="groups">{group(t4["excluded"])}</ul>
    <div class="wnote">答えの V00862 は「引用している規格」の 1 つで、たまたま最初の 19 本に入っていた。そのリレーションのプロパティ <span class="mono">{{{e(key_rel["props"][:70])}}}</span> も一緒に文脈へ渡る。いっぽう VTR-T005 は 61 本持っていて、最初の 19 本が全部「定義された計測項目」（DEFINED_IN {t5_in_types.get("DEFINED_IN", 0)} 本）で、引用先 {t5_ref_out} 本（V02313、QC-KAI-027 など）は 1 つも入らなかった。質問が T005 の引用先を問うものなら、この段で落ちる。</div></div>
</div></section>"""

# 文字列化・テキスト断片の選別
rows_html = "".join(
    f"<li class='{'key' if KEY in (r.get('keys_in_row') or []) else ''}'><b>{e(sh(r['source']))} の行</b>　本文 {len(r['chunks'])} チャンク ＋ Entities {r['n_entity_lines'] or '?'} 行 ＋ Relationships {r['n_rel_lines'] or '?'} 行 ＝ {r['len_chars']:,} 字　"
    f"{'<span class=hit>V00862 のリレーションを含む</span>' if KEY in (r.get('keys_in_row') or []) else '<span class=miss>V00862 なし</span>'}</li>" for r in rows)
pieces = r3["pieces"]
pieces_html = "".join(
    f"<li class='{'keep' if p['kept'] else 'drop'}'><span class='sc'>{p['sim']:.4f}</span><b>{e(sh(p['source']))}</b> {'<span class=tag>本文あり</span>' if p['has_text_content'] else '<span class=\"tag dim\">エンティティ・リレーションの途中</span>'} {p['len_chars']:,} 字 {'<span class=hit>V00862</span>' if KEY in p['keys_found'] else ''}<span class='hd'>{hd(p['head'], 36)}</span></li>" for p in pieces)
step4 = f"""
<section class="wstep"><div class="wh"><span class="wn">STEP 4</span><span class="wt">文字列化：文書ごとに 1 テキストにまとめ → 3000 トークンでテキスト断片化 → 質問との類似度で上位 4 テキスト断片だけ残す</span></div>
<div class="wgrid2">
  <div class="wbox"><div class="wl">4-a　文書行のテキスト（Text Content ＋ Entities ＋ Relationships）</div><ul class="rows">{rows_html}</ul>
    <div class="wnote">起点の展開結果は文書行ごとに束ねる。本文は STEP 1 で当たったチャンクだけで、残りはエンティティ名とリレーションの行。目録・T003 の行にも VTR-T004 の展開が入るので、V00862 のリレーションは 3 つの行に現れる。</div></div>
  <div class="wbox hi"><div class="wl">4-b　テキスト断片化 → ruri 類似度で並べ替え → <b>上位 4 テキスト断片</b>（全 {len(pieces)} 断片）</div><ul class="pieces">{pieces_html}</ul>
    <div class="wnote">残った 4 テキスト断片の合計 {r3['final_context_chars']:,} 字が LLM に渡る文脈のすべて。T001 の行は 2 断片とも落ち、目録の「本文あり」断片も落ちた（本文が無くても Entities/Relationships の途中だけの断片が残ることがある）。V00862 のリレーションは 4 テキスト断片すべてに含まれた。</div></div>
</div></section>"""
ans = " ".join(tr["answer"].split())
step5 = f"""
<section class="wstep"><div class="wh"><span class="wn">STEP 5</span><span class="wt">生成：gemma4 が「この文脈だけで答える」</span></div>
<div class="wbox"><div class="answer">{e(ans)}</div>
<div class="why" style="margin-top:8px"><span class="mark ok">○</span><p>チャンク本文のどこにも V00862 は無かった。STEP 3 で VTR-T004 の 19 本に <b>REFERENCES {{context: 『…試験方案:V00862』…}} → V00862</b> が入り、STEP 4 のテキスト断片の選別も通ったので、LLM はこの 1 行を根拠に答えた。20 経路の枠か上位 4 テキスト断片か、どちらか一方を外れても×になる。</p></div></div></section>"""

# ================= 対照 M12 =================
q12, d12, r12, t12 = "M12", D2["M12"], R3["M12"], T3["M12"]["runs"]["graph_vector_fulltext"]
ft = D1["M12"]["fulltext_top"]; fmax12 = max(x["score"] for x in ft); v12 = D1["M12"]["vector_top5"]; vmax12 = max(x["score"] for x in v12)
kept12 = [(c["doc"], c["pos"], c["score"]) for r in sorted(r12["rows"], key=lambda r: -max(c["score"] for c in r["chunks"])) for c in r["chunks"]]
p15 = next((x for x in ft if x["doc"] == "HS-VTR-T001E.pdf" and x["pos"] == 15), None)
t001 = next(x for x in d12["expansion"] if x["id"] == "VTR-T001")
jun_in = [i for i in t001["included"] if "潤滑油温度" in i["other"]]
seeds12 = {x["id"] for x in d12["seed_entities"]}
pieces12 = "".join(f"<li class='{'keep' if p['kept'] else 'drop'}'><span class='sc'>{p['sim']:.4f}</span><b>{e(sh(p['source']))}</b> {'<span class=tag>本文あり</span>' if p['has_text_content'] else '<span class=\"tag dim\">エンティティ・リレーションの途中</span>'} {p['len_chars']:,} 字<span class='hd'>{hd(p['head'], 30)}</span></li>" for p in r12["pieces"])
ans12 = " ".join(t12["answer"].split())
contrast = f"""
<div class="worked contrast">
  <div class="wq"><span class="qid">M12</span><h3>TPL.TPS形初号機の性能計測時、外部給油方式の過給機の潤滑油温度は何℃以下であることが求められますか？</h3><div class="gold"><strong>正解</strong>　潤滑油入口温度+45℃以下　／　③の回答 <span class="mark ng">×</span>　— 同じ手順のどこで落ちたか</div></div>
  <div class="wgrid4">
    <div class="wbox"><div class="wl">STEP 1　答えのチャンクが 4 件に入らない</div>
      <div class="wsmall">答え（3-6 潤滑油温度）は T001 p15。全文索引では 2 位（正規化 {p15['score']/fmax12:.3f}）だが、ベクトル側は別チャンクが 0.99 台で横一線に並び、統合 4 件は <b>{'・'.join(f'{sh(d)} p{p}' for d, p, _ in kept12)}</b>。p15 は落ちた。</div></div>
    <div class="wbox"><div class="wl">STEP 2　「潤滑油温度」は起点にならない</div>
      <div class="wsmall">4 チャンクの起点エンティティは {len(d12['seed_entities'])} 件。潤滑油温度(玉・コロ)・潤滑油温度(平軸受)（計測箇所の表由来、リレーション 3 本ずつ）は起点だが、JUDGED_BY のリレーションを持つ <b>潤滑油温度 は起点に無い</b>（p15 にしかぶら下がっていない）。</div></div>
    <div class="wbox"><div class="wl">STEP 3　名前だけ届き、JUDGED_BY のリレーションは 2 ホップ目</div>
      <div class="wsmall">潤滑油温度は、規格 VTR-T001（64 本）の入った 19 本の中に「VTR-T001 に定義された計測項目」として<b>名前だけ</b>届く。その潤滑油温度が持つ判定 JUDGED_BY {{when: 外部給油方式, value: 入口温度+45}} は起点から見て 2 段目なので、1 段止まりの展開では入らない。取り出し全体（エンティティ {t12 and len(t12['entities'])}・リレーション {len(t12['relationships'])}）に「+45」は 0 件。</div></div>
    <div class="wbox"><div class="wl">STEP 4・5　残った 4 テキスト断片にも無く「記載なし」</div>
      <ul class="pieces small">{pieces12}</ul>
      <div class="wsmall">回答：{e(ans12[:150])}…</div></div>
  </div>
  <div class="wnote">③の「JUDGED_BY のリレーションが届かない」の中身はこれ。答えを持つ項目のチャンクが STEP 1 で外れると、項目は隣の Standard 経由で名前だけ届き、値を持つリレーションは 1 ホップ先で止まる。M07（タービン入口ガス温度）・M15（±2%）も同じ形。</div>
</div>"""
frag = f"""
<div class="worked">
  <div class="wq"><span class="qid">M06</span><h3>発行目録に載っている RU・RH型初号機の基準では、性能計測はどの試験方案（図番）に基づきますか？</h3><div class="gold"><strong>正解</strong>　コンプレッサ性能計測標準試験方案 V00862　／　③の回答 <span class="mark ok">○</span>　— 本文に無い答えをリレーションが運んだ例</div></div>
  {step1}{step2}{step3}{step4}{step5}
</div>
{contrast}"""
open(f"{S}/fig_worked.html", "w", encoding="utf-8").write(frag)
print("fig_worked.html", len(frag), "chars; M06 final keys", r3["final_keys_found"], "pieces", len(pieces), "| M12 seeds", len(d12["seed_entities"]), "潤滑油温度 seed?", "潤滑油温度" in seeds12, "jun_in", [(i['type'], i['other']) for i in jun_in])
