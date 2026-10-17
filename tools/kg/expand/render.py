import json, html, collections
E = html.escape
d = json.load(open("m09.json", encoding="utf-8"))
T = d["totals"]; INK, INK2, INK3, RULE, ACC, ACC_SOFT = "#1B2430", "#4A5665", "#7A8794", "#C9D2DA", "#2a78d6", "#EAF2FC"

# ---------- 図1: 数の推移 ----------
stages = [("検索対象", "184 チャンク", "全文書", False), ("候補", "20 件", "ベクトル＋全文", False), ("選択", "6 件", "ruri でリランク", True),
          ("起点", f"{T['start_entities']} 個", "HAS_ENTITY で収集", False), ("展開", f"{T['retrieved_entities']} 個・{T['retrieved_rels']} 本", "1 ホップ、1 個 20 本まで", False),
          ("行", f"{T['lines_pooled']} 行", "文字列化・重複除去", False), ("残す", f"{T['lines_kept']} 行", "ruri でリランク", True), ("文脈", f"{T['context_chars']:,} 字", "文書ごとに区画", False)]
bw, gap, x0, y0, bh = 104, 18, 8, 30, 78
svg = [f'<svg viewBox="0 0 {x0*2 + 8*bw + 7*gap} 150" role="img" aria-label="M09 の検索で、各段に残った数の推移。質問の内容を見て選ぶのは、選択と残すの 2 段だけである。">',
       f'<defs><marker id="a" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="{INK2}"/></marker></defs>']
for i, (h, n, how, acc) in enumerate(stages):
    x = x0 + i * (bw + gap)
    svg.append(f'<rect x="{x}" y="{y0}" width="{bw}" height="{bh}" rx="5" fill="{ACC_SOFT if acc else "#FFFFFF"}" stroke="{ACC if acc else RULE}" stroke-width="{1.5 if acc else 1}"/>')
    svg.append(f'<text x="{x + bw/2}" y="{y0 + 18}" text-anchor="middle" font-size="11" fill="{INK3}">{E(h)}</text>')
    svg.append(f'<text x="{x + bw/2}" y="{y0 + 44}" text-anchor="middle" font-size="{12.5 if len(n) > 9 else 14}" font-weight="700" fill="{INK}">{E(n)}</text>')
    svg.append(f'<text x="{x + bw/2}" y="{y0 + 66}" text-anchor="middle" font-size="10" fill="{"#1c5cab" if acc else INK2}">{E(how)}</text>')
    if i < 7:
        svg.append(f'<line x1="{x + bw + 1}" y1="{y0 + bh/2}" x2="{x + bw + gap - 2}" y2="{y0 + bh/2}" stroke="{INK2}" stroke-width="1.5" marker-end="url(#a)"/>')
svg.append(f'<rect x="{x0}" y="124" width="14" height="14" rx="3" fill="{ACC_SOFT}" stroke="{ACC}" stroke-width="1.5"/><text x="{x0 + 20}" y="135" font-size="11.5" fill="{INK2}">質問の内容を見て選ぶ段</text>')
svg.append('</svg>')
SVG = "".join(svg)

def rows(items): return "".join(items)
picked = rows(f'<tr><td>{p["doc"]} の {p["pos"]} 番目</td><td class="num">{p["score"]:.3f}</td><td class="num">{p["chars"]}</td><td class="src">{E(p["head"][:60])}…</td></tr>' for p in d["picked"])
docs = rows(f'<tr><td>{doc}</td><td>{"、".join(str(c)+" 番目" for c in v["chunks"])}</td><td class="num">{v["n_start"]}</td><td class="src">{E("、".join(f"{k} {n}" for k, n in sorted(v["labels"].items(), key=lambda x: -x[1])))}</td></tr>' for doc, v in d["docs"].items())
hubs = {}
for h in T["hubs"]:
    hubs.setdefault(h["id"], {"label": h["label"], "deg": h["deg"], "docs": set()})["docs"].add(h["doc"])
hub_rows = rows(f'<tr><td>{E(k)}</td><td>{v["label"]}</td><td class="num">{v["deg"]}</td><td class="num">19</td><td class="num">{len(v["docs"])}</td></tr>' for k, v in sorted(hubs.items(), key=lambda x: -x[1]["deg"]))
blocks = rows(f'<tr><td>{b["doc"]}</td><td class="num">{b["chunks"]}</td><td class="num">{b["text_chars"]}</td><td class="num">{b["ent_lines"]}</td><td class="num">{b["rel_lines"]}</td></tr>' for b in d["context_blocks"])
tot_text = sum(b["text_chars"] for b in d["context_blocks"]); tot_e = sum(b["ent_lines"] for b in d["context_blocks"]); tot_r = sum(b["rel_lines"] for b in d["context_blocks"])
ent_by = "、".join(f"{k} {n}" for k, n in sorted(d["kept"]["ent_by_label"].items(), key=lambda x: -x[1]))
rel_by = "、".join(f"{k} {n}" for k, n in sorted(d["kept"]["rel_by_type"].items(), key=lambda x: -x[1]))
ans = rows(f"<li><code>{E(l[:120])}{'…' if len(l) > 120 else ''}</code></li>" for l in d["kept"]["answer_lines"])

page = f"""<title>既定モードのグラフ展開</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=BIZ+UDPGothic:wght@400;700&family=Noto+Sans+JP:wght@400;500;700&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
:root{{color-scheme:light;--bg:#FFFFFF;--panel:#F4F6F8;--ink:#1B2430;--ink2:#4A5665;--ink3:#7A8794;--rule:#C9D2DA;--rule2:#E4E9EE;--acc:#2a78d6;--acc-soft:#EAF2FC;
--head:"BIZ UDPGothic","Noto Sans JP","Hiragino Sans","Yu Gothic",sans-serif;--body:"Noto Sans JP","Hiragino Sans","Yu Gothic",system-ui,sans-serif;--mono:"IBM Plex Mono","Noto Sans JP",ui-monospace,Menlo,monospace}}
*{{box-sizing:border-box}} body{{background:var(--bg);color:var(--ink);font-family:var(--body);font-size:15px;line-height:1.8;margin:0}}
.wrap{{max-width:1000px;margin:0 auto;padding-inline:20px;padding-block:36px 72px}}
h1,h2,h3{{font-family:var(--head);margin:0;line-height:1.35;text-wrap:balance}} h1{{font-size:27px}}
h2{{font-size:20px;margin-top:46px;margin-bottom:12px;padding-bottom:7px;border-bottom:2px solid var(--ink)}} h2 small{{font-family:var(--mono);font-size:12px;color:var(--ink3);margin-left:10px;font-weight:400}}
p{{margin:0 0 10px;max-width:46em;text-wrap:pretty}}
.eyebrow{{font-family:var(--mono);font-size:12px;letter-spacing:.08em;color:var(--ink2);margin-bottom:8px}}
.why{{display:grid;grid-template-columns:6em 1fr;border:1px solid var(--rule);margin-top:18px}} .why dt,.why dd{{margin:0;padding:9px 14px;border-bottom:1px solid var(--rule2)}}
.why dt{{font-family:var(--head);font-weight:700;background:var(--panel);color:var(--ink2)}} .why dt:last-of-type,.why dd:last-of-type{{border-bottom:none}}
@media (max-width:560px){{.why{{grid-template-columns:1fr}}.why dt{{border-bottom:none;padding-bottom:0}}}}
.tbl{{overflow-x:auto;border:1px solid var(--rule)}} table{{border-collapse:collapse;width:100%;font-size:13.5px}} .tbl table{{min-width:640px}}
th,td{{padding:7px 11px;border-bottom:1px solid var(--rule2);text-align:left;vertical-align:top}} thead th{{font-family:var(--head);font-size:12.5px;color:var(--ink2);background:var(--panel);white-space:nowrap}}
tbody tr:last-child td{{border-bottom:none}} td.num{{font-family:var(--mono);text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}} td.src,.src{{color:var(--ink2);font-size:13px}}
tfoot td{{background:var(--panel);font-weight:700;border-top:1px solid var(--rule)}}
code{{font-family:var(--mono);font-size:12.5px;overflow-wrap:anywhere}} pre{{font-family:var(--mono);font-size:12.5px;line-height:1.6;background:var(--panel);border:1px solid var(--rule2);padding:10px 12px;margin:6px 0 10px;overflow-x:auto;white-space:pre-wrap}}
figure{{margin:0}} figcaption{{font-size:13px;color:var(--ink2);margin-top:8px;max-width:52em;text-wrap:pretty}}
.scroll{{overflow-x:auto;border:1px solid var(--rule);padding:8px}} .scroll svg{{display:block;width:100%;min-width:900px;height:auto}} .scroll svg text{{font-family:var(--body)}}
.q{{border-left:4px solid var(--ink);background:var(--panel);padding:10px 14px;margin:8px 0 14px;max-width:none}}
.note{{border-left:3px solid var(--acc);background:var(--panel);padding:8px 14px;margin:12px 0;font-size:14px}} .note p{{margin:0;max-width:none}}
ul.notes{{margin:6px 0 0;padding-left:1.2em}} ul.notes li{{margin-bottom:7px;max-width:50em}} ul.lines{{margin:4px 0 10px;padding-left:1.2em;font-size:13px}} ul.lines li{{margin-bottom:4px}}
.cases{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin:8px 0 12px}} @media (max-width:760px){{.cases{{grid-template-columns:1fr}}}}
.case{{border:1px solid var(--rule);padding:10px 12px;font-size:13.5px}} .case b{{font-family:var(--head);display:block;margin-bottom:4px}}
footer{{margin-top:52px;padding-top:12px;border-top:1px solid var(--rule);font-size:12.5px;color:var(--ink3);line-height:1.8}} footer p{{max-width:none;margin:0}}
</style>
<div class="wrap">
<header>
  <div class="eyebrow">フォーク版 GRAPH BUILDER ／ 既定モードの検索 ／ 2026-10-07</div>
  <h1>既定モードのグラフ展開</h1>
  <dl class="why">
    <dt>目的</dt><dd>既定モードが、起点のエンティティをどう選び、トリプルをどう絞り、最終的に何を LLM に渡しているかを、1 問の実行記録で示す。</dd>
    <dt>対象</dt><dd>現在の構成である。チャンクのリランクと行のリランクを追加した状態で、評価の 16 問を実行した記録を用いる。</dd>
    <dt>結論</dt><dd>質問の内容を見て選ぶのは、チャンクの選択と行の選別の 2 段だけである。起点の選択と 1 ホップ先の取り出しは、質問を見ずに機械的に決まる。</dd>
  </dl>
</header>

<h2>例題</h2>
<p class="q">RU.RH.RH3.AT形過給機について、単独試運転での吸込温度の計測点数と、初号機の管理カーブが何台目以降の判定基準になるかを答えてください。</p>
<p>正解は「2 点」と「4 台目以降」であり、この回の回答は両方とも正解であった。</p>

<h2>図 1　各段に残った数<small>M09</small></h2>
<figure><div class="scroll">{SVG}</div>
<figcaption>展開の 260 個と 543 本は、文書ごとの重複除去後に全文書を合わせた数である。行 803 は、エンティティとリレーションを文字列にして重複を除いた数である。</figcaption></figure>

<h2>段 1　チャンクの選択</h2>
<p>質問文でベクトル検索と全文検索を行い、候補 20 件を ruri のリランカーで並べ替えて上位 6 件を残す。</p>
<div class="tbl"><table><thead><tr><th>選ばれたチャンク</th><th>リランクの点数</th><th>文字数</th><th>本文の冒頭</th></tr></thead><tbody>{picked}</tbody></table></div>
<p class="src" style="margin-top:8px">答え「2 点」が書いてある T001E の 2 番目は、6 件に入っていない。</p>

<h2>段 2　起点のエンティティ</h2>
<p>選ばれた 6 件のチャンクを文書ごとにまとめる。文書ごとに、そのチャンク群に HAS_ENTITY で付いているエンティティを集め、「そのチャンク群のうち何件に付いているか」が多い順に並べ、先頭から最大 40 個を起点にする。質問との近さは見ない。この回は 1 文書あたりのチャンクが 1、2 件なので、件数はほぼ全部 1 で、並び順は実質決まっていない。</p>
<div class="tbl"><table><thead><tr><th>文書</th><th>選ばれたチャンク</th><th>起点の数</th><th>種類の内訳</th></tr></thead><tbody>{docs}</tbody>
<tfoot><tr><td colspan="2">合計</td><td class="num">{T['start_entities']}</td><td></td></tr></tfoot></table></div>
<p class="src" style="margin-top:8px">1 文書から選ばれるチャンクは 1、2 件なので、上限 40 個に達した文書はない。T005B の 8 番目だけで 34 個の起点がある。</p>

<h2>段 3　1 ホップ先の取り出し</h2>
<p>起点ごとに、質問とエンティティの埋め込みの類似度で 3 通りに分かれる。進む先はエンティティだけであり、チャンクと文書のノードには進まない。</p>
<div class="cases">
  <div class="case"><b>類似度 0.3 未満</b>展開しない。起点そのものだけを取り出す。</div>
  <div class="case"><b>0.3 以上 0.9 以下</b>1 ホップ以内の経路を 20 本まで。1 本目は起点自身なので、リレーションは 19 本である。順序は Neo4j の返却順で、質問は見ない。</div>
  <div class="case"><b>0.9 超</b>2 ホップ以内の経路を 40 本まで。この回は起点 2 個が該当し、2 ホップ先の行が文脈に混ざった。</div>
</div>
<p>リレーションが 19 本を超える起点では、どの 19 本が入るかが質問と無関係に固定される。この回の起点のうち該当するものは次のとおりである。</p>
<div class="tbl"><table><thead><tr><th>起点</th><th>種類</th><th>リレーションの本数</th><th>取り出せる本数</th><th>起点になった文書数</th></tr></thead><tbody>{hub_rows}</tbody></table></div>
<p class="src" style="margin-top:8px">同じエンティティが複数の文書で起点になっても、取り出せる 19 本は毎回同じである。VTR-T001 は 163 本のうち 19 本しか入らない。</p>
<div class="note"><p>この回で答え「2 点」を運んだ経路は、T005B の 8 番目の起点「空気吸込温度」から 1 ホップ先の規定 REQ-VTR-T001-p2-5 である。空気吸込温度のリレーションは 15 本で、上限に掛からず全部が取り出された。答えのチャンク本体は取得していない。</p></div>
<p>質問とエンティティの類似度を、同じ埋め込みモデルで計算し直した結果は次のとおりである。</p>
<div class="tbl"><table><thead><tr><th>対象</th><th>類似度の範囲</th><th>0.3 未満</th><th>0.9 超</th></tr></thead><tbody>
<tr><td>全エンティティ 671 個</td><td class="num">0.705 〜 0.924</td><td class="num">0 個</td><td class="num">2 個</td></tr>
<tr><td>この回の起点 74 個</td><td class="num">0.757 〜 0.924</td><td class="num">0 個</td><td class="num">2 個</td></tr>
</tbody></table></div>
<p style="margin-top:8px">0.9 を超えた起点は REQ-VTR-T004-2-1-1-1（0.924）と RU.RH.RH3.AT形過給機の初号機（0.913）である。この 2 個は 2 ホップ以内の経路を 40 本まで取り出すため、取り出された 543 本のうち 123 本、260 個のうち 37 個は起点から 2 ホップ離れたものであった。文脈に残った行にも、潤滑油温度の規定 3 行など 2 ホップ先の行が含まれる。</p>
<p class="src">埋め込みモデルが ruri の場合、類似度は 0.7 以上に偏る。0.3 未満の「展開しない」分岐は事実上働かず、0.9 超の 2 ホップ分岐は質問に近い起点で起こる。しきい値は本家の既定値のままで、ruri 向けに調整されていない。</p>

<h2>段 4　行の選別</h2>
<p>取り出したエンティティとリレーションを文字列の行にし、全文書の行を 1 つに集めて ruri のリランカーで質問に近い順に並べ、上位 120 行または 14,000 字まで残す。この回は {T['lines_pooled']} 行から {T['lines_kept']} 行を残し、{T['lines_pooled'] - T['lines_kept']} 行を捨てた。</p>
<div class="tbl"><table><thead><tr><th>残った行</th><th>内訳</th></tr></thead><tbody>
<tr><td>エンティティの行 {tot_e}</td><td class="src">{E(ent_by)}</td></tr>
<tr><td>リレーションの行 {tot_r}</td><td class="src">{E(rel_by)}</td></tr></tbody></table></div>
<p style="margin-top:8px">答えを持つ行は 3 行とも残った。</p>
<ul class="lines">{ans}</ul>
<p class="src">捨てた {T['lines_pooled'] - T['lines_kept']} 行の内容は記録していない。段 3 で取り出せなかったトリプルは、この段には現れない。</p>

<h2>段 5　LLM に渡す文脈</h2>
<p>残った行を元の文書の区画に戻し、文書ごとに本文、エンティティの行、リレーションの行の順で並べる。</p>
<div class="tbl"><table><thead><tr><th>文書の区画</th><th>チャンク</th><th>本文の文字数</th><th>エンティティの行</th><th>リレーションの行</th></tr></thead><tbody>{blocks}</tbody>
<tfoot><tr><td>合計</td><td class="num">6</td><td class="num">{tot_text:,}</td><td class="num">{tot_e}</td><td class="num">{tot_r}</td></tr></tfoot></table></div>
<p style="margin-top:8px">文脈は {T['context_chars']:,} 字で、本文は {tot_text:,} 字と全体の {round(100*tot_text/T['context_chars'])}% である。残りはエンティティとリレーションの行である。行の書式は次のとおりである。</p>
<pre>Document start
This Document belongs to the source VTR-T005B.pdf
Content: Text Content:
（チャンクの本文。複数あれば ---- で区切る）
----
Entities:
種類:名前 (説明文)
----
Relationships:
種類:名前 リレーションの種類 {{プロパティ}} 種類:名前
Document end</pre>
<p>LLM に渡らないものは次の 4 つである。</p>
<ul class="notes">
  <li>選ばれなかったチャンクの本文。1 ホップ先のエンティティに紐づくチャンクも取得しない。</li>
  <li>エンティティの説明文以外のプロパティ。規定レコードの値や単位は、説明文に含まれる範囲でのみ渡る。</li>
  <li>リレーションの点数や順位。行は五十音順に並ぶ。</li>
  <li>行がどのチャンクから来たかの対応。</li>
</ul>

<h2>課題の所在</h2>
<ul class="notes">
  <li><b>起点の選択は質問を見ない。</b>選ばれたチャンクの全エンティティが起点になる。この回は 87 個であった。</li>
  <li><b>19 本の選び方が固定である。</b>リレーションの多いエンティティでは、答えのトリプルが入るかどうかが作成順で決まる。質問を見る段 4 のリランクは、ここで捨てられたトリプルを選べない。</li>
  <li><b>文脈の 8 割が行である。</b>本文は 6 件で 1,888 字にとどまり、残りは 120 行の要約である。行に答えが書き写されている場合にだけ、文書をまたぐ事実が届く。</li>
</ul>
<footer><p>記録: 2026-09-14 の評価実行のログと trace_p1b.json、2026-10-07 時点の Neo4j。構成は候補 20 件、選択 6 件、起点上限 40、1 ホップ 20 経路、行の上限 120 行または 14,000 字。グラフは規定レコード方式で抽出したものである。</p></footer>
</div>
"""
assert "�" not in page
open("expand.html", "w", encoding="utf-8").write(page); print("bytes", len(page.encode()))
