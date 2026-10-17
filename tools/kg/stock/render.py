import html
E = html.escape
INK, INK2, INK3, RULE, ACC, ACC_SOFT = "#1B2430", "#4A5665", "#7A8794", "#C9D2DA", "#2a78d6", "#EAF2FC"
CUTS = [
 ("1", "チャンクの検索", "ベクトル索引と全文索引の結果を、それぞれ最高点で割って正規化し、ノードごとの最大値で並べて上位 k 件", "k = 4", "見る",
  "設定値は 5 だが、検索器へ渡すキー名が top_k で、langchain 側が受け取る名前は k である。渡らず既定の 4 件になる",
  "QA_integration.py 393・403・413、constants.py 302、neo4j_graphrag の naive ranker"),
 ("2", "点数のしきい値", "正規化した点数が 0.5 未満のチャンクを除外", "0.5", "見る",
  "最上位は常に 1.0 なので、最上位の半分に満たない点数のチャンクは 4 件の枠があっても落ちる",
  "constants.py 245、QA_integration.py 391"),
 ("3", "起点のエンティティ", "選ばれたチャンクを文書ごとにまとめ、そのチャンク群に HAS_ENTITY で付いているエンティティを「チャンク群のうち何件に付いているか」の多い順に並べ、先頭から最大 40 個", "40 個／文書", "見ない",
  "同数のときの順序は決まっていない。1 文書のチャンクが 1、2 件なら 40 個に達しないことが多いが、達した場合にどれが残るかは質問と無関係である",
  "constants.py 329、350〜351"),
 ("4", "展開するかの判定", "質問とエンティティの埋め込みの類似度で分岐。0.3 未満は展開なし、0.3〜0.9 は 1 ホップ、0.9 超は 2 ホップ", "0.3／0.9", "判定のみ",
  "0.3 未満の起点は隣を一切取らない。しきい値は埋め込みモデルに依存し、ruri では類似度が 0.7 以上に偏るため 0.3 未満は起きず、0.9 超は質問に近い起点で起こる",
  "constants.py 330〜333、353〜363"),
 ("5", "1 ホップ先の本数", "起点 1 個につき経路 20 本まで。1 本目は起点自身なので隣は 19 本。進む先はエンティティのみで、チャンクと文書には進まない", "20 本／起点", "見ない",
  "並び替えがなく LIMIT だけなので、どの 19 本かは Neo4j の返却順で固定される。隣が多いほど答えの行が入らない",
  "constants.py 357〜358"),
 ("6", "文字列化", "エンティティは「種類:名前 (説明)」、リレーションは「種類:名前 種類 種類:名前」の行にし、五十音順に並べる", "—", "見ない",
  "リレーションのプロパティと点数は出ない。行がどのチャンクから来たかも残らない",
  "constants.py 371〜395"),
 ("7", "断片への分割", "文書ごとの文字列を 3,000 トークンで分割する。符号は gpt2 で、日本語の本文なら 1 断片は約 1,600 字", "3,000 トークン", "見ない",
  "分割位置は内容と無関係で、エンティティ行とリレーション行の途中で切れる",
  "QA_integration.py 315、constants.py 246"),
 ("8", "断片の除外", "質問の埋め込みとの類似度が 0.10 未満の断片を除外", "0.10", "見る",
  "しきい値が低く、ここで落ちる断片は少ない",
  "QA_integration.py 317〜319、constants.py 247"),
 ("9", "断片の上限", "残った断片を類似度順に並べ、上位 4 断片だけを LLM に渡す。モデル名が登録済みなら 2 または 28", "4 断片", "見る",
  "文書の行が 5 断片に割れていれば、5 番目以降は丸ごと落ちる。答えの行がどの断片にあるかは運である",
  "QA_integration.py 182〜189、constants.py 249〜253"),
]
rows = "".join(f'<tr><td class="no">{n}</td><td><b>{E(h)}</b><div class="src">{E(what)}</div></td><td class="num">{E(v)}</td><td class="{ {"見る":"yes","判定のみ":"mid","見ない":"no"}[q] }">{q}</td><td class="src">{E(drop)}</td><td class="ref">{E(ref)}</td></tr>' for n, h, what, v, q, drop, ref in CUTS)

# 図: 9 段の流れ。質問を見る段を強調
svg = ['<svg viewBox="0 0 960 170" role="img" aria-label="標準の既定モードの 9 つの絞り込み段。質問の内容を見るのは 4 段で、起点の選択と 1 ホップ先の選び方は見ない。">',
       f'<defs><marker id="a" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="{INK2}"/></marker></defs>']
labels = [("チャンク検索", "4 件", "yes"), ("しきい値", "0.5", "yes"), ("起点", "40/文書", "no"), ("展開判定", "0.3/0.9", "mid"), ("1 ホップ", "20/起点", "no"), ("文字列化", "—", "no"), ("分割", "3,000 tok", "no"), ("断片除外", "0.10", "yes"), ("断片上限", "4 断片", "yes")]
bw, gap, x0, y0, bh = 96, 12, 8, 26, 78
for i, (h, v, k) in enumerate(labels):
    x = x0 + i * (bw + gap)
    fill = {"yes": ACC_SOFT, "mid": "#FFF6E5", "no": "#FFFFFF"}[k]; stroke = {"yes": ACC, "mid": "#C98500", "no": RULE}[k]
    svg.append(f'<rect x="{x}" y="{y0}" width="{bw}" height="{bh}" rx="5" fill="{fill}" stroke="{stroke}" stroke-width="{1.5 if k != "no" else 1}"/>')
    svg.append(f'<text x="{x + bw/2}" y="{y0 + 18}" text-anchor="middle" font-size="10.5" fill="{INK3}">段 {i + 1}</text>')
    svg.append(f'<text x="{x + bw/2}" y="{y0 + 40}" text-anchor="middle" font-size="12" font-weight="700" fill="{INK}">{E(h)}</text>')
    svg.append(f'<text x="{x + bw/2}" y="{y0 + 62}" text-anchor="middle" font-size="11" fill="{INK2}">{E(v)}</text>')
    if i < 8: svg.append(f'<line x1="{x + bw + 1}" y1="{y0 + bh/2}" x2="{x + bw + gap - 2}" y2="{y0 + bh/2}" stroke="{INK2}" stroke-width="1.5" marker-end="url(#a)"/>')
svg.append(f'<text x="{x0}" y="124" font-size="10.5" fill="{INK3}">Cypher で実行: 段 3 〜 6　／　Python で実行: 段 1・2・7 〜 9</text>')
svg.append(f'<rect x="{x0}" y="136" width="13" height="13" rx="3" fill="{ACC_SOFT}" stroke="{ACC}" stroke-width="1.5"/><text x="{x0 + 19}" y="147" font-size="11" fill="{INK2}">質問の内容を見る</text>')
svg.append(f'<rect x="{x0 + 150}" y="136" width="13" height="13" rx="3" fill="#FFF6E5" stroke="#C98500" stroke-width="1.5"/><text x="{x0 + 169}" y="147" font-size="11" fill="{INK2}">展開するかの判定にだけ使う</text>')
svg.append(f'<rect x="{x0 + 370}" y="136" width="13" height="13" rx="3" fill="#FFFFFF" stroke="{RULE}"/><text x="{x0 + 389}" y="147" font-size="11" fill="{INK2}">質問を見ない</text>')
svg.append('</svg>')
SVG = "".join(svg)

page = f"""<title>標準の既定モードの絞り込み箇所</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=BIZ+UDPGothic:wght@400;700&family=Noto+Sans+JP:wght@400;500;700&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
:root{{color-scheme:light;--bg:#FFFFFF;--panel:#F4F6F8;--ink:#1B2430;--ink2:#4A5665;--ink3:#7A8794;--rule:#C9D2DA;--rule2:#E4E9EE;--acc:#2a78d6;--acc-soft:#EAF2FC;--mid:#C98500;--mid-soft:#FFF6E5;
--head:"BIZ UDPGothic","Noto Sans JP","Hiragino Sans","Yu Gothic",sans-serif;--body:"Noto Sans JP","Hiragino Sans","Yu Gothic",system-ui,sans-serif;--mono:"IBM Plex Mono","Noto Sans JP",ui-monospace,Menlo,monospace}}
*{{box-sizing:border-box}} body{{background:var(--bg);color:var(--ink);font-family:var(--body);font-size:15px;line-height:1.8;margin:0}}
.wrap{{max-width:1040px;margin:0 auto;padding-inline:20px;padding-block:36px 72px}}
h1,h2{{font-family:var(--head);margin:0;line-height:1.35;text-wrap:balance}} h1{{font-size:27px}}
h2{{font-size:20px;margin-top:46px;margin-bottom:12px;padding-bottom:7px;border-bottom:2px solid var(--ink)}}
p{{margin:0 0 10px;max-width:48em;text-wrap:pretty}}
.eyebrow{{font-family:var(--mono);font-size:12px;letter-spacing:.08em;color:var(--ink2);margin-bottom:8px}}
.why{{display:grid;grid-template-columns:6em 1fr;border:1px solid var(--rule);margin-top:18px}} .why dt,.why dd{{margin:0;padding:9px 14px;border-bottom:1px solid var(--rule2)}}
.why dt{{font-family:var(--head);font-weight:700;background:var(--panel);color:var(--ink2)}} .why dt:last-of-type,.why dd:last-of-type{{border-bottom:none}}
@media (max-width:560px){{.why{{grid-template-columns:1fr}}.why dt{{border-bottom:none;padding-bottom:0}}}}
.tbl{{overflow-x:auto;border:1px solid var(--rule)}} table{{border-collapse:collapse;width:100%;font-size:13.5px}} .tbl table{{min-width:900px}}
th,td{{padding:8px 10px;border-bottom:1px solid var(--rule2);text-align:left;vertical-align:top}} thead th{{font-family:var(--head);font-size:12.5px;color:var(--ink2);background:var(--panel);white-space:nowrap}}
tbody tr:last-child td{{border-bottom:none}} td.no{{font-family:var(--mono);color:var(--ink3);white-space:nowrap}} td.num{{font-family:var(--mono);white-space:nowrap;font-variant-numeric:tabular-nums}}
.src{{color:var(--ink2);font-size:13px}} td.ref{{font-family:var(--mono);font-size:11.5px;color:var(--ink3);min-width:11em}}
td.yes{{color:#1c5cab;font-weight:700;white-space:nowrap}} td.mid{{color:var(--mid);font-weight:700;white-space:nowrap}} td.no{{white-space:nowrap}}
td.no.q{{color:var(--ink3)}}
.scroll{{overflow-x:auto;border:1px solid var(--rule);padding:8px}} .scroll svg{{display:block;width:100%;min-width:900px;height:auto}} .scroll svg text{{font-family:var(--body)}}
figure{{margin:0}} figcaption{{font-size:13px;color:var(--ink2);margin-top:8px;max-width:52em}}
.verdict{{border:1px solid var(--rule);border-left:5px solid var(--ink);padding:12px 16px;margin:14px 0}} .verdict p{{max-width:none;margin:0 0 6px}}
ul.notes{{margin:6px 0 0;padding-left:1.2em}} ul.notes li{{margin-bottom:7px;max-width:52em}}
footer{{margin-top:52px;padding-top:12px;border-top:1px solid var(--rule);font-size:12.5px;color:var(--ink3);line-height:1.8}} footer p{{max-width:none;margin:0;overflow-wrap:anywhere}}
</style>
<div class="wrap">
<header>
  <div class="eyebrow">NEO4J LLM GRAPH BUILDER ／ 本家 MAIN 2026-07-30 ／ 既定モード GRAPH_VECTOR_FULLTEXT</div>
  <h1>標準の既定モードの絞り込み箇所</h1>
  <dl class="why">
    <dt>目的</dt><dd>本家の Graph Builder が、既定モードの検索で必要なトリプルをどこで落とし得るかを、コードの該当行で特定する。</dd>
    <dt>経緯</dt><dd>ベンダーから「起点のエンティティ、関連エンティティ、1 ホップ先のいずれでも絞り込んでおり、必要なトリプルが落ちる箇所が多い」との見解があり、事実関係が議論になっていた。</dd>
    <dt>結論</dt><dd>見解は正しい。絞り込みは 9 段あり、うち質問の内容を見て選ぶのは 4 段である。起点の選択と 1 ホップ先の選び方は質問を見ずに決まる。さらに、ベンダーの指摘にない Python 側の 4 段でも落ちる。</dd>
  </dl>
</header>

<h2>図 1　9 段の流れ</h2>
<figure><div class="scroll">{SVG}</div>
<figcaption>既定値は本家コードのとおりである。段 1 の 4 件は、設定値 5 がキー名の不一致で渡らず、langchain の既定値になった結果である。</figcaption></figure>

<h2>各段の詳細と根拠</h2>
<div class="tbl"><table>
<thead><tr><th>段</th><th>何をするか</th><th>既定値</th><th>質問を見るか</th><th>落ち方</th><th>根拠（本家コードの行）</th></tr></thead>
<tbody>{rows}</tbody></table></div>
<p class="src" style="margin-top:8px">行番号は本家 main のコミット 5ff7af3（2026-07-30）の backend/src 配下である。段 1 の正規化は依存ライブラリ neo4j_graphrag の naive ranker による。</p>

<h2>ベンダーの見解との対応</h2>
<div class="verdict">
<p><b>「起点のエンティティで絞っている」</b>　正しい。段 3。文書ごとに 40 個で、並びは現れるチャンク数の順である。</p>
<p><b>「関連エンティティも絞っている」</b>　正しい。段 4 と段 5。類似度 0.3 未満の起点は展開されず、展開しても起点 1 個につき 19 本までである。</p>
<p><b>「1 ホップ先も絞っている」</b>　正しい。段 5。進む先はエンティティに限られ、チャンクと文書には進まない。隣のチャンクの本文は取得されない。</p>
<p><b>見解にない落ち方</b>　段 1・2 と段 7〜9 である。チャンクは 4 件で点数のしきい値もある。取り出した行は 3,000 トークンごとに割られ、上位 4 断片しか LLM に渡らない。</p>
</div>

<h2>実測で確認した落ち方</h2>
<p>過給機の基準 6 文書と評価 16 問で、本家と同じ既定の設定のまま実行した記録である。</p>
<ul class="notes">
  <li><b>段 1 で落ちた例。</b>T003 から T004 の定義をたどる問題で、T004 のチャンクが 4 件に 1 件も入らなかった。</li>
  <li><b>段 4 で 2 ホップ分岐が働いた例。</b>吸込温度の点数を問う問題で、起点 74 個のうち 2 個が類似度 0.9 を超え、取り出した 543 本のうち 123 本が起点から 2 ホップ先の行になった。</li>
  <li><b>段 5 で落ちた例。</b>目録から T004 の図番をたどる問題で、起点 VTR-T004 のリレーション 53 本のうち、図番への参照が固定の 19 本に入らなかった。</li>
  <li><b>段 9 で落ちた例。</b>T005 から T001 の判定値をたどる問題で、答えを持つ規定の行は取り出されていたが、その行を含む断片が上位 4 断片に残らなかった。</li>
  <li><b>文脈にあっても誤答した例。</b>2 問で、正しい規定の行が LLM に届いていたが、見落としや似た規定との取り違えで誤答した。</li>
</ul>
<p>16 問の結果は、グラフなしで 7 問正解、既定モードで 9 問正解であった。</p>

<h2>フォークで変更した箇所</h2>
<p>当方のフォークでは、段 1 と段 6〜9 を変更している。段 3〜5 は本家のままである。</p>
<ul class="notes">
  <li>段 1: 候補を 20 件取り、リランカーで並べ替えて 6 件を選ぶ。キー名の不一致も修正した。</li>
  <li>段 6: リレーションのプロパティを行に含める。</li>
  <li>段 7〜9: 断片への分割をやめ、取り出した全行をリランカーで並べて上位 120 行または 14,000 字を渡す。</li>
</ul>
<p>この変更で 16 問の正解は 15 問になったが、段 5 で落ちる問題はそのまま残っている。</p>

<footer><p>根拠: github.com/neo4j-labs/llm-graph-builder の main（5ff7af3、2026-07-30）、backend/src/QA_integration.py と backend/src/shared/constants.py。langchain_neo4j と neo4j_graphrag の検索クエリはバックエンドのイメージ内のコードで確認した。実測は tools/kg の評価記録による。</p></footer>
</div>
"""
assert "�" not in page
open("stock-cuts.html", "w", encoding="utf-8").write(page); print("bytes", len(page.encode()))
