import json, html
E = html.escape
K = "/home/uchida/ai-stack/llm-graph-builder/tools/kg"
R = json.load(open(f"{K}/chunkviz/raw.json", encoding="utf-8")); order = json.load(open(f"{K}/chunkviz/order.json"))["order"]
OC = {c["id"]: c["text"] for c in R["chunks"]}
old = lambda d, i: " ".join(OC[order[d][i - 1]].replace("�", "□").split())
new = json.load(open("new_chunks.json", encoding="utf-8"))
n35 = [c for c in new["HS-VTR-T001E.pdf"] if c["section_no"] == "3-5"][0]["text"]
o13, o14, o16 = old("HS-VTR-T001E.pdf", 13), old("HS-VTR-T001E.pdf", 14), old("HS-VTR-T001E.pdf", 16)
def cut(s, a, b): return ("…" if a else "") + s[a:b] + ("…" if b < len(s) else "")
counts = "".join(f"<tr><td>{E(n)}</td><td class='num'>{o}</td><td class='num'>{len(new[k])}</td></tr>" for n, o, k in (
    ("HS-VTR-T001E", 20, "HS-VTR-T001E.pdf"), ("HS-VTR-T002F", 20, "HS-VTR-T002F.pdf"), ("VTR-T003D", 9, "VTR-T003 D.pdf"), ("VTR-T004C", 14, "VTR-T004C.pdf"), ("VTR-T005B", 18, "VTR-T005B.pdf"),
    ("発行目録", 5, [x for x in new if "目録" in x][0])))
page = f"""<title>チャンク分割の見直し結果</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=BIZ+UDPGothic:wght@400;700&family=Noto+Sans+JP:wght@400;500;700&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
:root{{color-scheme:light;--bg:#FFFFFF;--panel:#F4F6F8;--ink:#1B2430;--ink2:#4A5665;--ink3:#7A8794;--rule:#C9D2DA;--rule2:#E4E9EE;--acc:#2a78d6;--acc-soft:#EAF2FC;--ok:#2E7D4F;--ng:#B23A2E;--ng-soft:#F8E4E1;
--head:"BIZ UDPGothic","Noto Sans JP","Hiragino Sans","Yu Gothic",sans-serif;--body:"Noto Sans JP","Hiragino Sans","Yu Gothic",system-ui,sans-serif;--mono:"IBM Plex Mono","Noto Sans JP",ui-monospace,Menlo,monospace}}
*{{box-sizing:border-box}} body{{background:var(--bg);color:var(--ink);font-family:var(--body);font-size:15px;line-height:1.8;margin:0}}
.wrap{{max-width:1000px;margin:0 auto;padding-inline:20px;padding-block:36px 72px}}
h1,h2,h3{{font-family:var(--head);margin:0;line-height:1.35;text-wrap:balance}} h1{{font-size:27px}}
h2{{font-size:20px;margin-top:46px;margin-bottom:12px;padding-bottom:7px;border-bottom:2px solid var(--ink)}} h3{{font-size:14.5px;margin-bottom:6px}}
p{{margin:0 0 10px;max-width:48em;text-wrap:pretty}}
.eyebrow{{font-family:var(--mono);font-size:12px;letter-spacing:.08em;color:var(--ink2);margin-bottom:8px}}
.why{{display:grid;grid-template-columns:6em 1fr;border:1px solid var(--rule);margin-top:18px}} .why dt,.why dd{{margin:0;padding:9px 14px;border-bottom:1px solid var(--rule2)}}
.why dt{{font-family:var(--head);font-weight:700;background:var(--panel);color:var(--ink2)}} .why dt:last-of-type,.why dd:last-of-type{{border-bottom:none}}
@media (max-width:560px){{.why{{grid-template-columns:1fr}}.why dt{{border-bottom:none;padding-bottom:0}}}}
.tbl{{overflow-x:auto;border:1px solid var(--rule)}} table{{border-collapse:collapse;width:100%;font-size:13.5px}} .tbl table{{min-width:560px}} .pair .tbl table{{min-width:0}}
th,td{{padding:7px 11px;border-bottom:1px solid var(--rule2);text-align:left;vertical-align:top}} thead th{{font-family:var(--head);font-size:12.5px;color:var(--ink2);background:var(--panel);white-space:nowrap}}
tbody tr:last-child td{{border-bottom:none}} td.num{{font-family:var(--mono);text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}} .src{{color:var(--ink2);font-size:13px}}
.pair{{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:14px}} @media (max-width:820px){{.pair{{grid-template-columns:1fr}}}}
.box{{border:1px solid var(--rule);min-width:0}} .box h3{{padding:8px 12px;background:var(--panel);margin:0;border-bottom:1px solid var(--rule2)}}
.box.new{{border-color:var(--acc)}} .box.new h3{{background:var(--acc-soft)}}
.chunk{{font-family:var(--mono);font-size:12px;line-height:1.65;padding:9px 12px;border-bottom:1px dashed var(--rule);white-space:pre-wrap;overflow-wrap:anywhere}} .chunk:last-child{{border-bottom:none}}
.chunk b{{font-family:var(--head);font-size:11.5px;color:var(--ink3);display:block;margin-bottom:2px;font-weight:700}} .bad{{background:var(--ng-soft)}}
ol.rules{{margin:6px 0;padding-left:1.4em}} ol.rules li{{margin-bottom:6px;max-width:50em}}
ul.notes{{margin:6px 0 0;padding-left:1.2em}} ul.notes li{{margin-bottom:7px;max-width:52em}}
.note{{border-left:3px solid var(--acc);background:var(--panel);padding:8px 14px;margin:12px 0;font-size:14px}} .note p{{margin:0;max-width:none}}
footer{{margin-top:52px;padding-top:12px;border-top:1px solid var(--rule);font-size:12.5px;color:var(--ink3);line-height:1.8}} footer p{{max-width:none;margin:0}}
</style>
<div class="wrap">
<header>
  <div class="eyebrow">フォーク版 GRAPH BUILDER ／ PDF の取り込み ／ 2026-10-11</div>
  <h1>チャンク分割の見直し結果</h1>
  <dl class="why">
    <dt>目的</dt><dd>過給機の基準 6 文書のチャンクを、項番の構造に沿って分割し直す。</dd>
    <dt>経緯</dt><dd>従来はトークン数で機械的に分割しており、文の途中での分断、文字化け、枠の文字の混入があった。</dd>
    <dt>結論</dt><dd>分割の品質は改善した。文字化けは解消し、表と項のまとまりが保たれ、未収録であった 3 ページも取り込まれた。一方、評価 16 問の正解数と正解グラフとの一致は従来と同等であり、今回の評価では差が表れなかった。</dd>
  </dl>
</header>

<h2>従来の分割の問題</h2>
<div class="tbl"><table><thead><tr><th>問題</th><th>該当数</th><th>原因</th></tr></thead><tbody>
<tr><td>文の途中で終わる</td><td class="num">86 個中 83 個</td><td>500 トークンごとに内容と無関係に分割する</td></tr>
<tr><td>文字化けを含む</td><td class="num">86 個中 50 個</td><td>トークンの境目が日本語 1 文字の途中に当たる</td></tr>
<tr><td>枠の文字が本文に混ざる</td><td class="num">86 個中 20 個</td><td>表題欄、配布先、改訂履歴、欄外の改訂番号を本文と区別しない</td></tr>
<tr><td>全角の英数字を含む</td><td class="num">86 個中 62 個</td><td>字形を揃えていない。「ＶＴＲ４」と「VTR4」が別の語になる</td></tr>
<tr><td>ページの未収録</td><td class="num">T002F の 3 ページ</td><td>1 ファイル 10,000 トークンで取り込みを打ち切る</td></tr>
<tr><td>別文書のチャンクが 1 個に統合される</td><td class="num">2 個</td><td>チャンクの ID が本文のハッシュ値であり、同文のページが同一ノードになる</td></tr>
</tbody></table></div>

<h2>新しい分割の規則</h2>
<ol class="rules">
  <li>ページごとに、表題欄、左の配布先欄、下の改訂履歴欄、欄外の改訂番号を本文から除く。枠の有無は、枠の語の位置から判定する。</li>
  <li>本文を「1.」「2-1」「3.2.1」などの項番の見出しで区切り、1 項 1 チャンクを基本とする。80 字未満の項は同じ章の次の項とまとめ、1,000 字を超える項は小項目または句点で分ける。</li>
  <li>各チャンクの先頭に、文書名、表題、章と項の見出しを付ける。</li>
  <li>本文中の表は、行ごとに「a | b | c」の形で残す。</li>
  <li>枠の文字は、文書ごとに 1 個の「文書情報」チャンクにまとめる。</li>
  <li>全角の英数字と半角カナは標準の字形に揃える。丸付き数字は変更しない。</li>
  <li>チャンクのノードに、項番、見出し、種別を保存する。</li>
</ol>
<p class="src">章番号は 1 ずつ増えるもの、項番号は現在の章番号で始まるものだけを見出しと認める。日付、数値、「2-1及び2-2項に示す」のような文は見出しにしない。見出しのない文書は、行の境目で上限まで詰める。</p>

<h2>実例　潤滑油供給条件の項</h2>
<p>T001E の「3-5 潤滑油供給条件」は、見出し、表、注記からなる。従来は 3 個のチャンクに分かれ、表は枠の文字と混ざって列の対応が失われていた。</p>
<div class="pair">
  <div class="box"><h3>従来（13・14・16 番目のチャンクの該当部分）</h3>
    <div class="chunk"><b>13 番目の末尾</b>{E(cut(o13, len(o13) - 95, len(o13)))}</div>
    <div class="chunk"><b>14 番目の冒頭</b>{E(cut(o14, 0, 150))}</div>
    <div class="chunk bad"><b>16 番目（枠の文字と表の値が混在）</b>{E(cut(o16, 20, 330))}</div>
  </div>
  <div class="box new"><h3>新（1 個のチャンク）</h3>
    <div class="chunk">{E(n35[:760])}…</div>
  </div>
</div>

<h2>新旧の比較</h2>
<div class="pair">
<div class="tbl"><table><thead><tr><th>指標</th><th>従来</th><th>新</th></tr></thead><tbody>
<tr><td>チャンク数</td><td class="num">86</td><td class="num">97</td></tr>
<tr><td>平均の字数</td><td class="num">268</td><td class="num">293</td></tr>
<tr><td>文字化けを含む</td><td class="num">50</td><td class="num">0</td></tr>
<tr><td>全角の英数字を含む</td><td class="num">62</td><td class="num">0</td></tr>
<tr><td>枠の文字が本文に混ざる</td><td class="num">20</td><td class="num">6</td></tr>
<tr><td>未収録のページ</td><td class="num">3</td><td class="num">0</td></tr>
<tr><td>項番を持つチャンク</td><td class="num">0</td><td class="num">83</td></tr>
</tbody></table></div>
<div class="tbl"><table><thead><tr><th>文書</th><th>従来</th><th>新</th></tr></thead><tbody>{counts}</tbody></table></div>
</div>
<p class="src" style="margin-top:8px">新で枠の文字が残る 6 個は、基準来歴の項と、巻末の改訂票のページである。</p>

<h2>評価への影響</h2>
<p>新しいチャンクから規定レコードを抽出し直し、同じ手順でグラフを作成して、同じ検索設定で 16 問を実行した。</p>
<div class="tbl"><table><thead><tr><th>指標</th><th>従来のチャンク</th><th>新しいチャンク</th></tr></thead><tbody>
<tr><td>回答に含まれた答えの事実</td><td class="num">22 個中 20</td><td class="num">22 個中 19</td></tr>
<tr><td>答えのチャンクがリランク上位 6 件に入った事実</td><td class="num">22 個中 19</td><td class="num">22 個中 19</td></tr>
<tr><td>正解グラフのエンティティの再現</td><td class="num">73 個中 58</td><td class="num">73 個中 55</td></tr>
<tr><td>正解グラフの文書間参照の再現</td><td class="num">11 本中 11</td><td class="num">11 本中 11</td></tr>
<tr><td>抽出された規定レコード</td><td class="num">187</td><td class="num">210</td></tr>
</tbody></table></div>
<ul class="notes">
  <li><b>点数は同等である。</b>差の 1 個は LLM の回答の揺れの範囲である。M06 は正解に変わり、M07 と M09 は不正解に変わった。M09 は答えの行が文脈にあったが、LLM が読み落とした。</li>
  <li><b>差が表れない理由。</b>16 問の答えは、従来のチャンクでも 22 個中 19 個が上位 6 件に入っていた。残る 3 個は、複数の事実を問う質問と図番の対応であり、分割の品質とは別の原因である。</li>
  <li><b>M07 で判明した点。</b>T001E には、タービン入口ガス温度について管理範囲の項（2-9）と判定基準の項（3-7）がある。新しい分割では 3-7 が単独のチャンクとなって最上位に入り、LLM は 3-7 を回答した。</li>
</ul>

<h2>作業中に判明した事項</h2>
<ul class="notes">
  <li><b>項をまとめると、抽出の出力が上限を超える。</b>潤滑油供給条件の表を 1 個のチャンクにした結果、規定レコードの抽出が 2,048 トークンの上限で途切れ、その項の規定が 0 件になった。上限を 6,000 に上げ、途切れた出力から完結した分を拾うよう修正し、20 件を取得した。</li>
  <li><b>項番により、参照を項のチャンクに結べる。</b>項や別表まで指定した参照 12 件のうち 7 件を、参照先のチャンクに特定できた。「HS-VTR-T001 の 3-5 項」は T001E の 13 番目のチャンクに対応する。</li>
  <li><b>見出しの付け方はチャンクの選択に影響しない。</b>先頭の見出しを、表題つき、文書名のみ、なしの 3 通りでリランクしたが、上位 6 件に入る答えの事実はいずれも 19 個であった。</li>
</ul>

<h2>残る課題</h2>
<ul class="notes">
  <li>図や数式のページは、図中の文字が順不同で並ぶ。</li>
  <li>見出しのない別紙は、直前の項に続けて取り込まれる。</li>
  <li>抽出器が、チャンク先頭の見出しを他文書への参照として拾う場合がある。</li>
  <li>枠の判定はこの文書群の様式の語に依存する。他の様式の文書では確認が必要である。</li>
  <li>分割方法の切り替えは環境変数で行う。画面からの指定には未対応である。</li>
</ul>
<div class="note"><p>評価に使用してきたグラフは変更していない。新しいチャンクのグラフは、別に起動した Neo4j（neo4j-kgb2）に作成した。</p></div>
<footer><p>実装: backend/src/document_sources/structured_pdf.py。取り込みへの組み込みは local_file.py、create_chunks.py、make_relationships.py。有効化は backend/.env の PDF_CHUNK_STRATEGY=structured。評価の記録は tools/kg の trace_set_default.json と trace_v2_newchunk.json。</p></footer>
</div>
"""
assert "�" not in page
open("chunking-result.html", "w", encoding="utf-8").write(page); print("bytes", len(page.encode()))
