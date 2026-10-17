import json, html, re
E = html.escape
K = "/home/uchida/ai-stack/llm-graph-builder/tools/kg"
D = json.load(open(f"{K}/diag_retriever_p4.json", encoding="utf-8")); M12 = D["M12"]
T = {r["id"]: r for r in json.load(open(f"{K}/trace_p1b.json", encoding="utf-8"))}; run = T["M09"]["runs"]["graph_vector_fulltext"]
ctx = run["context"].replace("�", "")
SHORT = {"HS-VTR-T001E.pdf": "T001E", "VTR-T005B.pdf": "T005B"}
kept = [p for p in M12["pieces"] if p["kept"]]
prow = "".join(f'<tr class="{"keep" if p["kept"] else "drop"}"><td class="num">{p["rank"]}</td><td>{SHORT.get(p["source"], p["source"])}</td><td class="num">{p["sim"]:.4f}</td><td class="num">{p["len_chars"]:,}</td><td>{"本文と行" if p["has_text_content"] else "行のみ"}</td><td>{"答えあり" if "+45" in p["keys_found"] else ""}</td><td>{"渡す" if p["kept"] else "落とす"}</td></tr>' for p in M12["pieces"])
head = E(M12["final_context_head"].replace("�", "")).replace("\n", "\n")

# M09 全文: 答えの行を強調
ANS = ["REQ-VTR-T001-p2-5 (", "REQ-VTR-T005-4-1-1 (", "REQ-VTR-T005-4-1-1 HAS_CRITERION"]
lines = []
for l in ctx.split("\n"):
    e = E(l)
    if any(a in l for a in ANS): e = f'<mark>{e}</mark>'
    elif l.startswith("Document start") or l.startswith("Document end") or l.startswith("This Document belongs") or l in ("----", "Entities:", "Relationships:") or l.startswith("Content: Text Content:"):
        e = f'<span class="frame">{e}</span>'
    lines.append(e)
full = "\n".join(lines)
blocks = ctx.count("Document start"); n_ent = sum(1 for b in ctx.split("----\nEntities:\n")[1:] for l in b.split("----\nRelationships:")[0].split("\n") if l.strip())
n_rel = sum(1 for b in ctx.split("----\nRelationships:\n")[1:] for l in b.split("\nDocument end")[0].split("\n") if l.strip() and not l.startswith("Document"))
text_chars = sum(len(b.split("Content: Text Content:\n", 1)[1].split("\n----\nEntities:")[0]) for b in ctx.split("Document start")[1:] if "Content: Text Content:\n" in b)

page = f"""<title>LLM に渡る文脈の実例</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=BIZ+UDPGothic:wght@400;700&family=Noto+Sans+JP:wght@400;500;700&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
:root{{color-scheme:light;--bg:#FFFFFF;--panel:#F4F6F8;--ink:#1B2430;--ink2:#4A5665;--ink3:#7A8794;--rule:#C9D2DA;--rule2:#E4E9EE;--acc:#2a78d6;--acc-soft:#EAF2FC;--ng:#B23A2E;--ng-soft:#F8E4E1;--ok:#2E7D4F;--ok-soft:#E2F1E7;
--head:"BIZ UDPGothic","Noto Sans JP","Hiragino Sans","Yu Gothic",sans-serif;--body:"Noto Sans JP","Hiragino Sans","Yu Gothic",system-ui,sans-serif;--mono:"IBM Plex Mono","Noto Sans JP",ui-monospace,Menlo,monospace}}
*{{box-sizing:border-box}} body{{background:var(--bg);color:var(--ink);font-family:var(--body);font-size:15px;line-height:1.8;margin:0}}
.wrap{{max-width:1000px;margin:0 auto;padding-inline:20px;padding-block:36px 72px}}
h1,h2{{font-family:var(--head);margin:0;line-height:1.35;text-wrap:balance}} h1{{font-size:27px}}
h2{{font-size:20px;margin-top:46px;margin-bottom:12px;padding-bottom:7px;border-bottom:2px solid var(--ink)}}
p{{margin:0 0 10px;max-width:48em;text-wrap:pretty}}
.eyebrow{{font-family:var(--mono);font-size:12px;letter-spacing:.08em;color:var(--ink2);margin-bottom:8px}}
.why{{display:grid;grid-template-columns:6em 1fr;border:1px solid var(--rule);margin-top:18px}} .why dt,.why dd{{margin:0;padding:9px 14px;border-bottom:1px solid var(--rule2)}}
.why dt{{font-family:var(--head);font-weight:700;background:var(--panel);color:var(--ink2)}} .why dt:last-of-type,.why dd:last-of-type{{border-bottom:none}}
@media (max-width:560px){{.why{{grid-template-columns:1fr}}.why dt{{border-bottom:none;padding-bottom:0}}}}
.q{{border-left:4px solid var(--ink);background:var(--panel);padding:10px 14px;margin:8px 0 14px;max-width:none}}
.tbl{{overflow-x:auto;border:1px solid var(--rule)}} table{{border-collapse:collapse;width:100%;font-size:13.5px}} .tbl table{{min-width:640px}}
th,td{{padding:7px 10px;border-bottom:1px solid var(--rule2);text-align:left;vertical-align:top}} thead th{{font-family:var(--head);font-size:12.5px;color:var(--ink2);background:var(--panel);white-space:nowrap}}
tbody tr:last-child td{{border-bottom:none}} td.num{{font-family:var(--mono);text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}}
tr.keep td:last-child{{color:var(--ok);font-weight:700}} tr.drop td{{color:var(--ink3)}} tr.drop td:last-child{{color:var(--ng);font-weight:700}}
.ctx{{font-family:var(--mono);font-size:12.3px;line-height:1.65;background:var(--panel);border:1px solid var(--rule);padding:12px 14px;margin:8px 0 10px;white-space:pre-wrap;overflow-wrap:anywhere}}
.ctx .frame{{color:var(--ink3)}} .ctx mark{{background:#FFF1B8;color:var(--ink);padding:0 2px}}
.legend{{font-size:13px;color:var(--ink2);margin:6px 0 10px}} .legend mark{{background:#FFF1B8;padding:0 4px}}
.note{{border-left:3px solid var(--acc);background:var(--panel);padding:8px 14px;margin:12px 0;font-size:14px}} .note p{{margin:0;max-width:none}}
.src{{color:var(--ink2);font-size:13px}}
ul.notes{{margin:6px 0 0;padding-left:1.2em}} ul.notes li{{margin-bottom:7px;max-width:52em}}
details{{margin:10px 0}} summary{{cursor:pointer;font-family:var(--head);font-weight:700;font-size:14px}}
footer{{margin-top:52px;padding-top:12px;border-top:1px solid var(--rule);font-size:12.5px;color:var(--ink3);line-height:1.8}} footer p{{max-width:none;margin:0}}
</style>
<div class="wrap">
<header>
  <div class="eyebrow">フォーク版 GRAPH BUILDER ／ 既定モード ／ 検索結果の文脈 ／ 2026-10-07</div>
  <h1>LLM に渡る文脈の実例</h1>
  <dl class="why">
    <dt>目的</dt><dd>既定モードの検索を経て LLM に渡る文脈が、実際にどのような文字列かを実例で示す。</dd>
    <dt>記録</dt><dd>本家と同じ標準設定の記録には、断片の一覧と文脈の先頭 600 字が残っている。全文が残っているのは現在の構成の記録である。両方を示す。</dd>
    <dt>要点</dt><dd>文脈は文書ごとの区画に、チャンクの本文、エンティティの行、リレーションの行を並べた文字列である。本文は全体の 2 割以下で、残りは行である。</dd>
  </dl>
</header>

<h2>例 1　標準設定での断片の選別と文脈の先頭</h2>
<p class="q">TPL.TPS形初号機の性能計測時、外部給油方式の過給機の潤滑油温度は何℃以下であることが求められますか？</p>
<p>正解は「潤滑油入口温度 +45℃以下」である。標準設定では、検索結果を文書ごとの文字列にし、3,000 トークンの断片に割り、質問との類似度順に上位 4 断片を LLM に渡す。この問題では 11 断片ができ、4 断片が渡った。</p>
<div class="tbl"><table>
<thead><tr><th>順位</th><th>文書</th><th>類似度</th><th>文字数</th><th>内容</th><th>答え</th><th>扱い</th></tr></thead>
<tbody>{prow}</tbody></table></div>
<p style="margin-top:8px">答え「+45」を含む行は 6 位の断片にあった。4 位との類似度の差は 0.009 であり、この差で断片ごと落ちた。渡った 4 断片のうち 2 つは本文を含まず、行だけの断片である。LLM は「情報なし」と答えた。</p>
<p>渡った文脈の先頭 600 字は次のとおりである。全体は {M12["final_context_chars"]:,} 字であった。</p>
<div class="ctx">{head}
<span class="frame">（以下略。記録は先頭 600 字のみ）</span></div>

<h2>例 2　現在の構成での文脈の全文</h2>
<p class="q">RU.RH.RH3.AT形過給機について、単独試運転での吸込温度の計測点数と、初号機の管理カーブが何台目以降の判定基準になるかを答えてください。</p>
<p>正解は「2 点」と「4 台目以降」である。現在の構成は、チャンク 6 件と、取り出した行をリランクした上位 120 行を渡す。この回の文脈は {len(ctx):,} 字、文書の区画 {blocks} つ、本文 {text_chars:,} 字、エンティティの行 {n_ent}、リレーションの行 {n_rel} であった。LLM は両方とも正解した。</p>
<p class="legend">黄色の <mark>強調</mark> は答えを運んだ 3 行である。薄い灰色は区画の枠を示す行である。それ以外はすべて LLM に渡った内容そのままである。</p>
<div class="ctx">{full}</div>

<h2>読み方</h2>
<ul class="notes">
  <li><b>区画は文書ごとである。</b>「Document start」から「Document end」までが 1 文書で、本文、Entities、Relationships の順に並ぶ。</li>
  <li><b>本文は選ばれたチャンクそのものである。</b>例 2 では 6 件で {text_chars:,} 字と、全体の {round(100*text_chars/len(ctx))}% にとどまる。</li>
  <li><b>Entities の行は「種類:名前 (説明文)」である。</b>答え「2 点」は、取得していない T001E の 2 番目の本文ではなく、規定を表すエンティティの説明文として届いている。</li>
  <li><b>Relationships の行は「種類:名前 リレーション {{プロパティ}} 種類:名前」である。</b>プロパティの表示はフォークで追加したもので、本家では種類だけである。</li>
  <li><b>行がどのチャンクから来たかは分からない。</b>区画の文書までしか分からず、行の順序は五十音順である。</li>
</ul>

<footer><p>例 1 は 2026-09-06 の診断記録 diag_retriever_p4.json（設定: 候補 5 件の指定、しきい値 0.5、断片 3,000 トークン、上位 4 断片。チャンクは 4 件）。例 2 は 2026-09-14 の評価記録 trace_p1b.json（候補 20 件をリランクして 6 件、行 120 まで）。どちらもグラフは規定レコード方式で抽出したものである。</p></footer>
</div>
"""
assert "�" not in page
open("context-example.html", "w", encoding="utf-8").write(page); print("bytes", len(page.encode()), "ctx chars", len(ctx), "blocks", blocks, "ents", n_ent, "rels", n_rel, "text", text_chars)
