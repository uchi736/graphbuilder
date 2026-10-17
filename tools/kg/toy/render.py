import html
E = html.escape
INK, INK2, INK3, RULE, ACC, ACC_SOFT, NG, NG_SOFT, OK, OK_SOFT, MID, MID_SOFT = "#1B2430", "#4A5665", "#7A8794", "#C9D2DA", "#2a78d6", "#EAF2FC", "#B23A2E", "#F8E4E1", "#2E7D4F", "#E2F1E7", "#C98500", "#FFF6E5"

def node(x, y, w, label, kind="ent"):
    fill = {"ent": "#FFFFFF", "doc": ACC_SOFT, "chunk": "#F4F6F8", "hub": "#FFF6E5"}[kind]
    stroke = {"ent": RULE, "doc": ACC, "chunk": INK3, "hub": MID}[kind]
    rx = 14 if kind != "chunk" else 4
    return (f'<rect x="{x}" y="{y}" width="{w}" height="28" rx="{rx}" fill="{fill}" stroke="{stroke}" stroke-width="1.3"/>'
            f'<text x="{x + w/2}" y="{y + 18.5}" text-anchor="middle" font-size="12" fill="{INK}">{E(label)}</text>')

def edge(x1, y1, x2, y2, label="", dash=False, color=INK2, marker=True, lx=None, ly=None):
    s = f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="1.4"{" stroke-dasharray=\"4 3\"" if dash else ""}{" marker-end=\"url(#ar)\"" if marker else ""}/>'
    if label:
        mx, my = (x1 + x2) / 2 if lx is None else lx, (y1 + y2) / 2 - 5 if ly is None else ly
        s += f'<text x="{mx}" y="{my}" text-anchor="middle" font-size="10.5" fill="{color}" paint-order="stroke" stroke="#FFFFFF" stroke-width="3">{E(label)}</text>'
    return s

# ---------- 図1 架空のグラフ ----------
g = [f'<svg viewBox="0 0 960 330" role="img" aria-label="架空の社内規定 3 本から作ったグラフ。チャンク 4 個、エンティティ 11 個、文書番号のエンティティ「出張規程」に 60 本の線が集まる。">',
     f'<defs><marker id="ar" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="{INK2}"/></marker></defs>']
# chunks row (top)
chunks = {"A1": (40, 20, 150, "A1 日当の条文"), "A2": (230, 20, 170, "A2 航空券の手配の条文"), "B1": (470, 20, 190, "B1 ビジネスクラスの承認"), "C1": (720, 20, 150, "C1 宿泊費の上限")}
for k, (x, y, w, l) in chunks.items(): g.append(node(x, y, w, l, "chunk"))
g.append(f'<text x="40" y="12" font-size="10.5" fill="{INK3}">チャンク</text>')
# entities row (middle)
ents = {"出張規程": (40, 110, 110, "hub"), "日当": (170, 110, 70, "ent"), "課長": (255, 110, 70, "ent"), "航空券": (345, 110, 80, "ent"), "総務部": (445, 110, 80, "ent"), "部長": (545, 110, 70, "ent"), "承認権限表": (640, 110, 110, "doc"), "経費規程": (770, 110, 100, "doc"), "宿泊費": (880, 110, 70, "ent")}
for k, (x, y, w, kind) in ents.items(): g.append(node(x, y, w, k, kind))
g.append(f'<text x="40" y="100" font-size="10.5" fill="{INK3}">エンティティ（HAS_ENTITY は破線）</text>')
def cxy(d, k, top=False):
    x, y, w = d[k][0], d[k][1], d[k][2]; return (x + w / 2, y if top else y + 28)
def has(ch, en):
    (x1, y1), (x2, y2) = cxy(chunks, ch), cxy(ents, en, top=True); return edge(x1, y1, x2, y2, dash=True, color=INK3, marker=False)
for ch, en in [("A1", "出張規程"), ("A1", "日当"), ("A1", "課長"), ("A2", "出張規程"), ("A2", "航空券"), ("A2", "総務部"), ("A2", "承認権限表"), ("B1", "航空券"), ("B1", "部長"), ("B1", "承認権限表"), ("C1", "経費規程"), ("C1", "宿泊費")]:
    g.append(has(ch, en))
# relationships (bottom arcs drawn as lines between entity bottoms via small routing)
def rel(a, b, label, color=INK2, dy=0):
    (x1, y1), (x2, y2) = cxy(ents, a), cxy(ents, b)
    y = 175 + dy
    return (f'<path d="M{x1},{y1} L{x1},{y} L{x2},{y} L{x2},{y2 + 2}" fill="none" stroke="{color}" stroke-width="1.4" marker-end="url(#ar)"/>'
            f'<text x="{(x1 + x2) / 2}" y="{y - 4}" text-anchor="middle" font-size="10.5" fill="{color}" paint-order="stroke" stroke="#FFFFFF" stroke-width="3">{E(label)}</text>')
g.append(rel("航空券", "総務部", "手配", dy=0))
g.append(rel("航空券", "部長", "クラスの承認", color=OK, dy=22))
g.append(rel("出張規程", "日当", "規定", dy=44))
g.append(rel("経費規程", "宿泊費", "規定", dy=0))
# hub fan: 出張規程 has 60 lines to 第1条…第60条
hx, hy = 95, 138
for i, (dx, lab) in enumerate([(-40, "第1条"), (-10, "第2条"), (20, "第3条"), (60, "…"), (100, "第60条")]):
    x2 = hx + dx + 10; y2 = 290
    g.append(f'<line x1="{hx}" y1="{hy}" x2="{x2}" y2="{y2 - 14}" stroke="{MID}" stroke-width="1.2"/>')
    g.append(f'<text x="{x2}" y="{y2}" text-anchor="middle" font-size="10.5" fill="{INK2}">{lab}</text>')
g.append(f'<text x="250" y="292" font-size="11" fill="{MID}">出張規程には全条文から 60 本の線が集まる</text>')
g.append(f'<text x="560" y="292" font-size="11" fill="{OK}">緑の線が答え 2 を持つトリプル</text>')
g.append(f'<text x="40" y="322" font-size="10.5" fill="{INK3}">水色は文書番号のエンティティ、黄色は線の多いエンティティ。線の種類名と向きは抽出時に LLM が決める。</text>')
g.append('</svg>'); G1 = "".join(g)

# ---------- 図2 段 5 の 19 本の抽選 ----------
s = [f'<svg viewBox="0 0 960 120" role="img" aria-label="起点「出張規程」の 60 本の線を作成順に並べ、先頭 19 本だけが取り出される。必要な第 25 条の線は 25 番目で入らない。">']
for i in range(60):
    x = 20 + i * 15.3; taken = i < 19; need = (i == 24)
    fill = OK_SOFT if taken else ("#FFFFFF"); stroke = OK if taken else RULE
    if need: fill, stroke = NG_SOFT, NG
    s.append(f'<rect x="{x}" y="34" width="11" height="34" rx="2" fill="{fill}" stroke="{stroke}" stroke-width="{1.6 if need else 1}"/>')
    if i in (0, 18, 24, 59): s.append(f'<text x="{x + 5.5}" y="86" text-anchor="middle" font-size="10" fill="{NG if need else INK2}">{i + 1}</text>')
s.append(f'<text x="20" y="22" font-size="11.5" fill="{INK}">起点「出張規程」の線 60 本を作成順に並べたもの</text>')
s.append(f'<text x="20" y="110" font-size="11" fill="{OK}">緑: 取り出される 19 本</text><text x="200" y="110" font-size="11" fill="{NG}">赤: 答えに必要な第 25 条の線。25 番目なので入らない</text><text x="620" y="110" font-size="11" fill="{INK3}">順序は質問と無関係で、毎回同じ</text>')
s.append('</svg>'); G2 = "".join(s)

def bar(name, v, keep, note):
    w = int(v * 260)
    return (f'<tr><td>{E(name)}</td><td class="barcell"><div class="bar" style="width:{w}px;background:{OK_SOFT if keep else NG_SOFT};border-color:{OK if keep else NG}"></div><span class="mono">{v:.2f}</span></td>'
            f'<td class="{"ok" if keep else "ng"}">{"渡る" if keep else "落ちる"}</td><td class="src">{E(note)}</td></tr>')
bars = bar("A1 日当の条文", 1.00, True, "質問の語「日当」「課長」「海外出張」と重なる") + bar("A2 航空券の手配の条文", 0.80, True, "「航空券」が重なる") + bar("C1 宿泊費の上限", 0.60, True, "「出張」の話題で近い。答えとは無関係") + bar("B1 ビジネスクラスの承認", 0.45, False, "質問に「ビジネスクラス」の語がなく点数が低い。しきい値 0.5 未満")

page = f"""<title>既定モードの動き方の例</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=BIZ+UDPGothic:wght@400;700&family=Noto+Sans+JP:wght@400;500;700&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
:root{{color-scheme:light;--bg:#FFFFFF;--panel:#F4F6F8;--ink:#1B2430;--ink2:#4A5665;--ink3:#7A8794;--rule:#C9D2DA;--rule2:#E4E9EE;--acc:#2a78d6;--acc-soft:#EAF2FC;--ok:#2E7D4F;--ok-soft:#E2F1E7;--ng:#B23A2E;--ng-soft:#F8E4E1;--mid:#C98500;--mid-soft:#FFF6E5;
--head:"BIZ UDPGothic","Noto Sans JP","Hiragino Sans","Yu Gothic",sans-serif;--body:"Noto Sans JP","Hiragino Sans","Yu Gothic",system-ui,sans-serif;--mono:"IBM Plex Mono","Noto Sans JP",ui-monospace,Menlo,monospace}}
*{{box-sizing:border-box}} body{{background:var(--bg);color:var(--ink);font-family:var(--body);font-size:15px;line-height:1.8;margin:0}}
.wrap{{max-width:1000px;margin:0 auto;padding-inline:20px;padding-block:36px 72px}}
h1,h2,h3{{font-family:var(--head);margin:0;line-height:1.35;text-wrap:balance}} h1{{font-size:27px}}
h2{{font-size:20px;margin-top:46px;margin-bottom:12px;padding-bottom:7px;border-bottom:2px solid var(--ink)}} h3{{font-size:15.5px}}
p{{margin:0 0 10px;max-width:48em;text-wrap:pretty}}
.eyebrow{{font-family:var(--mono);font-size:12px;letter-spacing:.08em;color:var(--ink2);margin-bottom:8px}}
.why{{display:grid;grid-template-columns:6em 1fr;border:1px solid var(--rule);margin-top:18px}} .why dt,.why dd{{margin:0;padding:9px 14px;border-bottom:1px solid var(--rule2)}}
.why dt{{font-family:var(--head);font-weight:700;background:var(--panel);color:var(--ink2)}} .why dt:last-of-type,.why dd:last-of-type{{border-bottom:none}}
@media (max-width:560px){{.why{{grid-template-columns:1fr}}.why dt{{border-bottom:none;padding-bottom:0}}}}
.q{{border-left:4px solid var(--ink);background:var(--panel);padding:10px 14px;margin:8px 0 14px;max-width:none}}
.tbl{{overflow-x:auto;border:1px solid var(--rule)}} table{{border-collapse:collapse;width:100%;font-size:13.5px}} .tbl table{{min-width:620px}}
th,td{{padding:7px 10px;border-bottom:1px solid var(--rule2);text-align:left;vertical-align:top}} thead th{{font-family:var(--head);font-size:12.5px;color:var(--ink2);background:var(--panel);white-space:nowrap}}
tbody tr:last-child td{{border-bottom:none}} td.num{{font-family:var(--mono);text-align:right;white-space:nowrap}} .src,td.src{{color:var(--ink2);font-size:13px}}
td.ok{{color:var(--ok);font-weight:700;white-space:nowrap}} td.ng{{color:var(--ng);font-weight:700;white-space:nowrap}}
.barcell{{white-space:nowrap}} .bar{{display:inline-block;height:14px;border:1px solid;border-radius:2px;vertical-align:middle;margin-right:8px}} .mono{{font-family:var(--mono);font-size:12.5px}}
figure{{margin:0}} figcaption{{font-size:13px;color:var(--ink2);margin-top:8px;max-width:52em}}
.scroll{{overflow-x:auto;border:1px solid var(--rule);padding:8px}} .scroll svg{{display:block;width:100%;min-width:900px;height:auto}} .scroll svg text{{font-family:var(--body)}}
.stage{{display:grid;grid-template-columns:7.5em 1fr;border:1px solid var(--rule);margin-top:12px}} .stage .lab{{background:var(--panel);padding:12px 14px;font-family:var(--head);font-weight:700;font-size:14px}}
.stage .lab small{{display:block;font-family:var(--body);font-weight:400;font-size:12px;color:var(--ink2);margin-top:4px}} .stage .body{{padding:12px 14px;min-width:0}} .stage .body p{{max-width:none;margin-bottom:6px}}
.stage.yes .lab{{border-left:5px solid var(--acc)}} .stage.no .lab{{border-left:5px solid var(--rule)}} .stage.mid .lab{{border-left:5px solid var(--mid)}}
@media (max-width:620px){{.stage{{grid-template-columns:1fr}}}}
pre{{font-family:var(--mono);font-size:12.5px;line-height:1.6;background:var(--panel);border:1px solid var(--rule2);padding:10px 12px;margin:6px 0 8px;overflow-x:auto;white-space:pre-wrap}}
pre .hit{{background:#FFF1B8}}
ul.notes{{margin:6px 0 0;padding-left:1.2em}} ul.notes li{{margin-bottom:7px;max-width:52em}}
.legend{{font-size:13px;color:var(--ink2);margin:8px 0}} .legend i{{display:inline-block;width:12px;height:12px;border-radius:2px;vertical-align:middle;margin:0 4px 0 10px}}
footer{{margin-top:52px;padding-top:12px;border-top:1px solid var(--rule);font-size:12.5px;color:var(--ink3);line-height:1.8}} footer p{{max-width:none;margin:0}}
</style>
<div class="wrap">
<header>
  <div class="eyebrow">NEO4J LLM GRAPH BUILDER ／ 既定モードの検索 ／ 架空の例による説明 ／ 2026-10-07</div>
  <h1>既定モードの動き方の例</h1>
  <dl class="why">
    <dt>目的</dt><dd>本家 Graph Builder の既定モードが、質問から LLM へ渡す文脈を組み立てる手順を、架空の小さな例で追う。</dd>
    <dt>前提</dt><dd>文書と数値は説明のために作ったものである。手順と既定値は本家コードのとおりである。</dd>
    <dt>要点</dt><dd>質問を見て選ぶのはチャンクの検索と断片の選別だけである。起点のエンティティと 1 ホップ先の 19 本は、質問と無関係に決まる。検索で落ちたチャンクの事実は、トリプルの行に書き写されている場合だけ届く。</dd>
  </dl>
</header>

<h2>材料</h2>
<div class="tbl"><table><thead><tr><th>文書</th><th>チャンク</th><th>本文の要点</th></tr></thead><tbody>
<tr><td>出張規程</td><td>A1</td><td>海外出張の日当は課長 8,000 円、部長 10,000 円とする</td></tr>
<tr><td>出張規程</td><td>A2</td><td>航空券の手配は総務部が行う。座席のクラスは承認権限表による</td></tr>
<tr><td>承認権限表</td><td>B1</td><td>航空券のビジネスクラスは部長の承認を要する</td></tr>
<tr><td>経費規程</td><td>C1</td><td>宿泊費の上限は 1 泊 12,000 円とする</td></tr>
</tbody></table></div>
<p style="margin-top:12px">抽出済みのグラフは次のとおりである。</p>
<figure><div class="scroll">{G1}</div>
<figcaption>図 1　チャンクはエンティティに HAS_ENTITY で接続し、エンティティ同士はトリプルで結ばれる。文書番号のエンティティ「出張規程」には、全条文から 60 本の線が集まっている。</figcaption></figure>
<p class="q" style="margin-top:18px">質問: 課長が海外出張するとき、日当はいくらで、航空券のクラスは誰の承認が要るか。</p>
<p>答えは 2 つである。答え 1 の 8,000 円は A1 に、答え 2 の部長は B1 にある。</p>

<h2>9 段の動き</h2>
<p class="legend">左の帯の色: <i style="background:var(--acc)"></i>質問の内容を見る　<i style="background:var(--mid)"></i>展開するかの判定にだけ使う　<i style="background:var(--rule)"></i>質問を見ない</p>

<div class="stage yes"><div class="lab">段 1・2<small>チャンクの検索と、しきい値</small></div><div class="body">
<p>質問の埋め込みと全文検索でチャンクを並べ、上位 4 件を取る。点数は最上位を 1.00 として正規化し、0.5 未満を捨てる。</p>
<div class="tbl"><table><thead><tr><th>チャンク</th><th>正規化した点数</th><th>結果</th><th>理由</th></tr></thead><tbody>{bars}</tbody></table></div>
<p style="margin-top:8px"><b>答え 2 を持つ B1 はここで落ちる。</b>以後、B1 の本文は二度と現れない。</p></div></div>

<div class="stage no"><div class="lab">段 3<small>起点のエンティティ</small></div><div class="body">
<p>残った 3 件のチャンクを文書ごとにまとめる。文書ごとに、そのチャンク群に HAS_ENTITY で付いているエンティティを集め、「そのチャンク群のうち何件に付いているか」が多い順に並べ、先頭から最大 40 個を起点にする。質問との近さは見ない。</p>
<div class="tbl"><table><thead><tr><th>出張規程の区画のエンティティ</th><th>付いているチャンク</th><th>件数</th><th>順位</th></tr></thead><tbody>
<tr><td>出張規程</td><td>A1、A2</td><td class="num">2</td><td>1 位</td></tr>
<tr><td>日当、課長、海外出張</td><td>A1</td><td class="num">1</td><td>2 位以降。同数の順は決まっていない</td></tr>
<tr><td>航空券、総務部、承認権限表、部長</td><td>A2</td><td class="num">1</td><td>同上</td></tr>
</tbody></table></div>
<p style="margin-top:8px">8 個しかないので上限 40 には届かず、全部が起点になる。上限が効くのは、1 つの文書から選ばれたチャンク群に 40 個を超えるエンティティが付いているときである。その場合、同数のエンティティのどれが残るかは決まっておらず、質問とも無関係である。</p>
<p>経費規程の区画は C1 だけなので、起点は経費規程、宿泊費、上限の 3 個である。答えと無関係な「宿泊費」も起点になる。</p></div></div>

<div class="stage mid"><div class="lab">段 4<small>展開するかの判定</small></div><div class="body">
<p>起点ごとに、質問との類似度で分岐する。0.3 未満は展開しない。0.3 から 0.9 は 1 ホップを 20 本まで。0.9 超は 2 ホップを 40 本まで。この例では全起点が 0.3 から 0.9 の範囲とする。</p>
<p class="src">実際の値の分布は埋め込みモデルで変わる。当方の ruri では 0.3 未満は起きず、0.9 超が質問に近い起点で起こる。</p></div></div>

<div class="stage no"><div class="lab">段 5<small>1 ホップ先の 19 本</small></div><div class="body">
<p>起点ごとに隣の線を取る。経路 20 本のうち 1 本は起点自身なので、線は 19 本である。並び替えがなく、どの 19 本かは作成順で決まる。</p>
<div class="tbl"><table><thead><tr><th>起点</th><th>線の本数</th><th>取れる本数</th><th>結果</th></tr></thead><tbody>
<tr><td>航空券</td><td class="num">3</td><td class="num">3</td><td><b>「航空券 —クラスの承認→ 部長」が入る。</b>B1 から抽出された答え 2 のトリプルである</td></tr>
<tr><td>宿泊費</td><td class="num">2</td><td class="num">2</td><td>無関係な行が入る</td></tr>
<tr><td>出張規程</td><td class="num">60</td><td class="num">19</td><td>第 1 条から第 19 条の線だけが入る。第 25 条の線は入らない</td></tr>
</tbody></table></div>
<figure style="margin-top:10px"><div class="scroll">{G2}</div>
<figcaption>図 2　線の多い起点では、必要な線が入るかどうかが作成順で決まる。質問を変えても同じ 19 本である。</figcaption></figure>
<p style="margin-top:8px">答え 2 が「出張規程」側の線にしか付いていなければ、この抽選に漏れて届かない。どちらに付くかは、抽出のときに LLM が決める。</p></div></div>

<div class="stage no"><div class="lab">段 6<small>文字列化</small></div><div class="body">
<p>本文と、集めたエンティティとトリプルを、文書ごとに 1 つの文字列にする。エンティティは「種類:名前 (説明)」、トリプルは「種類:名前 線の種類 種類:名前」の行である。行は五十音順に並び、どのチャンクから来たかは残らない。</p>
<pre>Document start
This Document belongs to the source 出張規程
Content: Text Content:
海外出張の日当は課長 8,000 円、部長 10,000 円とする
----
航空券の手配は総務部が行う。座席のクラスは承認権限表による
----
Entities:
Entity:航空券 (出張時の交通手段)
Entity:課長
Entity:部長
…（約 30 行）
----
Relationships:
<span class="hit">Entity:航空券 クラスの承認 Entity:部長</span>
Entity:航空券 手配 Entity:総務部
Entity:出張規程 規定 Entity:第1条
…（約 40 行）
Document end</pre></div></div>

<div class="stage yes"><div class="lab">段 7〜9<small>断片への分割と選別</small></div><div class="body">
<p>文書ごとの文字列を 3,000 トークンで割る。出張規程は 2 断片、経費規程は 1 断片になった。質問との類似度が 0.10 未満の断片を捨て、類似度順に上位 4 断片を LLM に渡す。この例は 3 断片なので全部が渡る。</p>
<p class="src">出張規程の行が長くて 5 断片に割れ、答えの行が 5 番目の断片にあれば、断片ごと落ちる。分割の位置は内容と無関係で、行の途中で切れることもある。</p></div></div>

<div class="stage yes"><div class="lab">回答<small>LLM</small></div><div class="body">
<p>LLM は、日当 8,000 円を A1 の本文から読み、承認者の部長を「航空券 クラスの承認 部長」の行から読む。B1 の本文は届いていないが、トリプルに書き写されていたので答えられた。</p></div></div>

<h2>この例から分かること</h2>
<ul class="notes">
  <li><b>質問を見て選ぶのは、段 1・2 と段 7〜9 だけである。</b>段 3 の起点と段 5 の 19 本は、質問と無関係に決まる。</li>
  <li><b>検索で落ちたチャンクの事実は、トリプルの行として届く場合だけ答えられる。</b>本文は届かない。トリプルが「誰の承認か」のような短い事実なら足りるが、手順や説明の文章は運べない。</li>
  <li><b>線の多いエンティティでは、必要なトリプルが入るかが作成順で決まる。</b>文書番号や「初号機」のような共通語がこれに当たる。</li>
  <li><b>最後の断片の選別で、取り出した行が丸ごと落ちることがある。</b>行が多い文書ほど断片が増え、答えの行が後ろの断片に入る確率が上がる。</li>
</ul>
<footer><p>手順と既定値は本家 main（5ff7af3、2026-07-30）の backend/src/QA_integration.py と shared/constants.py による。文書、点数、本数は説明のための架空の値である。実データでの同じ手順の追跡は「既定モードのグラフ展開」のページにある。</p></footer>
</div>
"""
assert "�" not in page
open("toy-example.html", "w", encoding="utf-8").write(page); print("bytes", len(page.encode()))
