# 前提「通常のハイブリッド検索で解けない問い」に対して、チャンク起点(既定モード)とエンティティ起点(entity_vector)が何を上積みするか → origins.html
import json, html
S = "/tmp/claude-1000/-home-uchida/4fdbd8d5-c462-43e6-88f0-fb9cf37aba38/scratchpad/std"
EX = json.load(open(f"{S}/explain.json", encoding="utf-8"))
def e(s): return html.escape(str(s if s is not None else ""))
def cls(m): return {"○": "ok", "△": "mid", "×": "ng"}.get(m, "")
def mark(m, small=False): return f"<span class='mark {cls(m)}{' sm' if small else ''}'>{e(m)}</span>"
def tally(k):
    ms = [EX[q][k]["mark"] for q in EX]; return f"○{ms.count('○')}　△{ms.count('△')}　×{ms.count('×')}"
SHORT = {
 "M01": "外部給油方式で長時間停止時の供給圧力は何 kPa 以下か", "M02": "TPL.TPS 初号機で規定圧まで上げるのは運転開始の何分前か",
 "M03": "T003 が挙げる RU.RH 要領で Nt はどう定義されるか", "M04": "T003 が挙げる VTR4 要領でサージラインの判定は何 % か",
 "M05": "目録の TPL.TPS 基準で性能計測はどの試験方案か", "M06": "目録の RU・RH 基準で性能計測はどの試験方案か",
 "M07": "目録→T003 が 1 番目に挙げる基準でタービン入口ガス温度の管理範囲", "M08": "TPL.TPS の吸込温度の点数と nMmax の出典",
 "M09": "RU.RH の吸込温度の点数と管理カーブが何台目以降か", "M10": "管理曲線の承認者と管理カーブの決裁者は同じか",
 "M11": "3 要領に共通の初品管理規定と各試験方案の図番", "M12": "TPL.TPS 初号機、外部給油方式の潤滑油温度は何 ℃ 以下か",
 "M13": "管理曲線基準の主管部門は目録と一致するか、部門名は", "M14": "TPS 初号機のインシュレーションと吸込温度の点数",
 "M15": "目録 2 番目の基準の回転数判定 ±% と外部振動の測定箇所", "M16": "VTR0.1 の外部振動はどこで測るか（1 ホップ、対照）",
}
BASE_OK = [q for q in sorted(EX) if EX[q]["base"]["mark"] == "○"]
PREMISE = [q for q in sorted(EX) if EX[q]["base"]["mark"] != "○"]
# 前提 9 問での上積みの仕組み（1 行）
MECH = {
 "M03": ("なし", "Nt の定義は本文 1 段落と条件ノード名にしかない。チャンク起点はそのチャンクが当たらず、エンティティ起点は名前で当てたが文書の橋渡しが無く保留（△）。"),
 "M06": ("チャンク起点のトリプル（③）", "当たった T004・目録のチャンクにぶら下がる規格 VTR-T004 の隣に REFERENCES → V00862 があり、そのプロパティ context ごと文脈へ。④では規格ノードの線が 40 本増えて 19 本からこぼれ×。"),
 "M07": ("チャンク起点のトリプル（④）", "目録→T003 のチャンクから規格 VTR-T001 を経て、規定ノード REQ-VTR-T001-2-9-7「タービン入口ガス温度 範囲 0~-10%」の説明文が届いた。③は判定のトリプルが届かず×。"),
 "M09": ("チャンク起点のトリプル（③④）", "2 点は本文に無く、空気吸込温度 MEASURED_AT {points: 2点, when: RU.RH…}（③）／規定ノード p2-5（④）が展開で届いた。エンティティ起点は点数の規定が上位 10 に入らず△。"),
 "M10": ("両方（④）", "決裁側の「検査部門長の決裁を得て配布」が規定ノードとして抽出済み。チャンク起点は展開で、エンティティ起点は規定文の直撃で届いた。"),
 "M11": ("なし（④チャンク起点で△）", "3 文書×2 事実。④は Standard→Standard の REFERENCES 3 本で QC-KAI-027 が「共通」と分かったが図番が揃わず。1 回の検索では届かない。"),
 "M12": ("エンティティ起点", "「潤滑油温度 ≤ 入口温度+45℃（外部給油方式）」が規定文として抽出済みで、質問と同義。本文では表の 1 行で、チャンク起点は 4 件に入れられず、④の展開でも断片選別で落ちた。"),
 "M13": ("エンティティ起点", "主管部門は別紙・表紙の欄にしか無く本文検索で埋もれる。別紙から作った規定ノードがエンティティ検索に当たり、元チャンクも付いた。"),
 "M15": ("なし", "±2% の判定チャンクが当たらず、③は判定トリプルが 2 段目で届かず、④は届いたが隣の 1% の規定を選んだ。エンティティ起点は上位 10 に入らず。"),
}
rows = ""
for q in PREMISE:
    who, why = MECH[q]
    rows += (f"<tr><td class='qid'>{q}</td><td>{e(SHORT[q])}</td><td class='c'>{mark(EX[q]['base']['mark'], True)}</td>"
             f"<td class='c'>{mark(EX[q]['p3']['mark'], True)}</td><td class='c'>{mark(EX[q]['p4']['mark'], True)}</td><td class='c'>{mark(EX[q]['p4ev']['mark'], True)}</td>"
             f"<td class='who'>{e(who)}</td><td class='why'>{e(why)}</td></tr>")
def cnt(k, qs, m="○"): return sum(1 for q in qs if EX[q][k]["mark"] == m)
solved_any = [q for q in PREMISE if any(EX[q][k]["mark"] == "○" for k in ("p3", "p4", "p4ev"))]
base_ok_rows = "".join(f"<li><span class='qid'>{q}</span> {e(SHORT[q])}<span class='qw'>{e(EX[q]['base']['why'])}</span>"
                       + (f"<span class='warn'>④チャンク起点では {EX[q]['p4']['mark']}、④エンティティ起点では {EX[q]['p4ev']['mark']}</span>" if (EX[q]['p4']['mark'] != '○' or EX[q]['p4ev']['mark'] != '○') else "")
                       + "</li>" for q in BASE_OK)
page = f"""<title>チャンク起点とエンティティ起点</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=BIZ+UDPGothic:wght@400;700&family=Noto+Sans+JP:wght@400;500;700&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
:root{{--bg:#F3F5F7;--paper:#FFFFFF;--ink:#1B2430;--ink2:#4A5665;--ink3:#7A8794;--rule:#CBD3DC;--rule2:#E3E8EE;--acc:#1F4E79;--acc-soft:#E4EDF6;
 --ok:#2E7D4F;--ok-soft:#E3F1E8;--mid:#A86A12;--mid-soft:#F6ECD9;--ng:#B3312F;--ng-soft:#F7E3E2;--ans:#F4F6F8;--c1:#8A5A1E;--c1-soft:#F6EEDF;--c2:#1F4E79;--c2-soft:#E4EDF6;
 --mono:"IBM Plex Mono","Noto Sans JP",ui-monospace,Menlo,monospace;--head:"BIZ UDPGothic","Noto Sans JP","Hiragino Sans","Yu Gothic",sans-serif;--body:"Noto Sans JP","Hiragino Sans","Yu Gothic",system-ui,sans-serif}}
@media (prefers-color-scheme: dark){{:root:not([data-theme="light"]){{--bg:#12181F;--paper:#1A222C;--ink:#E4E9EF;--ink2:#AEB9C5;--ink3:#7E8A97;--rule:#37424E;--rule2:#28323D;--acc:#8BBBE8;--acc-soft:#1E2E40;--ok:#6FCB93;--ok-soft:#173225;--mid:#E0A94A;--mid-soft:#3A2C12;--ng:#F08A86;--ng-soft:#3F1F1F;--ans:#202832;--c1:#E0B070;--c1-soft:#332A1C;--c2:#8BBBE8;--c2-soft:#1E2E40}}}}
:root[data-theme="dark"]{{--bg:#12181F;--paper:#1A222C;--ink:#E4E9EF;--ink2:#AEB9C5;--ink3:#7E8A97;--rule:#37424E;--rule2:#28323D;--acc:#8BBBE8;--acc-soft:#1E2E40;--ok:#6FCB93;--ok-soft:#173225;--mid:#E0A94A;--mid-soft:#3A2C12;--ng:#F08A86;--ng-soft:#3F1F1F;--ans:#202832;--c1:#E0B070;--c1-soft:#332A1C;--c2:#8BBBE8;--c2-soft:#1E2E40}}
*{{box-sizing:border-box}} body{{background:var(--bg);color:var(--ink);font-family:var(--body);font-size:15px;line-height:1.75;margin:0}}
.wrap{{max-width:1040px;margin:0 auto;padding:36px 24px 80px}}
h1,h2,h3{{font-family:var(--head);text-wrap:balance;margin:0}} h1{{font-size:30px;line-height:1.3}}
h2{{font-size:21px;margin:52px 0 14px;padding-bottom:8px;border-bottom:2px solid var(--acc);display:flex;align-items:baseline;gap:12px}} h2 .no{{font-family:var(--mono);font-size:13px;color:var(--acc)}}
h3{{font-size:17px;line-height:1.5}} p{{margin:0 0 12px;max-width:72ch}} .lede{{font-size:16px;color:var(--ink2);max-width:70ch}}
.eyebrow{{font-family:var(--mono);font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:var(--acc);margin-bottom:8px}}
.mono{{font-family:var(--mono);font-size:12.5px}}
.mark{{display:inline-flex;align-items:center;justify-content:center;width:26px;height:26px;border-radius:50%;border:2px solid currentColor;font-family:var(--head);font-weight:700;font-size:15px;line-height:1;flex:none}}
.mark.sm{{width:20px;height:20px;font-size:12px;border-width:1.5px}}
.mark.ok{{color:var(--ok);background:var(--ok-soft)}} .mark.mid{{color:var(--mid);background:var(--mid-soft)}} .mark.ng{{color:var(--ng);background:var(--ng-soft)}}
.tallies{{display:flex;flex-wrap:wrap;gap:10px;margin:18px 0 0}} .tally{{background:var(--paper);border:1px solid var(--rule);padding:10px 14px;min-width:200px}}
.tally .k{{font-size:12px;color:var(--ink3);font-family:var(--mono)}} .tally .v{{font-family:var(--head);font-size:20px;font-variant-numeric:tabular-nums}} .tally.base{{border-left:4px solid var(--ink3)}}
.gloss{{margin:16px 0 0;padding:10px 14px;border:1px dashed var(--rule);background:var(--paper);font-size:13px}} .gloss .gl{{font-family:var(--mono);font-size:11px;letter-spacing:.1em;color:var(--acc)}}
.gloss dl{{margin:4px 0 0;display:grid;grid-template-columns:auto 1fr;gap:2px 12px}} .gloss dt{{font-family:var(--head);font-weight:700;white-space:nowrap}} .gloss dd{{margin:0;color:var(--ink2)}}
.pane{{background:var(--paper);border:1px solid var(--rule);padding:16px 18px}} .pane p:last-child{{margin:0}}
ul.plain{{margin:0;padding:0;list-style:none;display:grid;gap:6px}} ul.plain li{{background:var(--paper);border:1px solid var(--rule2);padding:8px 12px;font-size:13.5px}}
.qid{{font-family:var(--mono);font-size:12.5px;color:var(--acc);font-weight:500;margin-right:6px}}
.qw{{display:block;font-size:12.5px;color:var(--ink2)}} .warn{{display:block;font-size:12px;color:var(--mid)}}
table{{border-collapse:collapse;width:100%;background:var(--paper);font-size:13.5px}} .tbl{{overflow-x:auto;border:1px solid var(--rule)}}
th,td{{padding:8px 10px;border-bottom:1px solid var(--rule2);text-align:left;vertical-align:top}} th{{font-family:var(--head);font-size:12.5px;color:var(--ink2);background:var(--ans);white-space:nowrap}}
td.c,th.c{{text-align:center;width:66px}} td.who{{white-space:nowrap;font-family:var(--head);font-weight:700;font-size:13px}} td.why{{font-size:12.5px;color:var(--ink2);line-height:1.6;min-width:280px}}
tbody tr:last-child td{{border-bottom:none}} tfoot td{{background:var(--ans);font-family:var(--head);font-weight:700}}
.two{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin:16px 0}} @media (max-width:760px){{.two{{grid-template-columns:1fr}}}}
.origin{{background:var(--paper);border:1px solid var(--rule);border-top:4px solid var(--c1);padding:16px 18px}} .origin.ent{{border-top-color:var(--c2)}}
.origin h3 small{{font-family:var(--mono);font-size:12px;color:var(--ink3);font-weight:400;margin-left:8px}}
.pipe{{display:grid;gap:6px;margin:10px 0}} .pipe .st{{display:grid;grid-template-columns:76px 1fr;gap:10px;font-size:13.5px;line-height:1.6}}
.pipe .st b{{font-family:var(--mono);font-size:11.5px;color:var(--ink3);letter-spacing:.06em;padding-top:3px}} .pipe .st .v{{background:var(--ans);padding:6px 10px;border-radius:2px}}
.origin.chunk .pipe .st .v.hl{{background:var(--c1-soft)}} .origin.ent .pipe .st .v.hl{{background:var(--c2-soft)}}
.cases{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px;margin-top:14px}} @media (max-width:900px){{.cases{{grid-template-columns:1fr}}}}
.case{{background:var(--paper);border:1px solid var(--rule);padding:14px 16px}} .case.c1{{border-top:4px solid var(--c1)}} .case.c2{{border-top:4px solid var(--c2)}} .case.c0{{border-top:4px solid var(--ng)}}
.case h3{{font-size:16px;margin-bottom:6px}} .case .cond{{font-size:13.5px;margin:0 0 8px}} .case ul{{margin:0;padding-left:1.1em;font-size:13px;color:var(--ink2)}} .case li{{margin:3px 0}}
.case .ex{{font-family:var(--mono);font-size:12px;color:var(--ink3);display:block;margin-top:8px}}
.note{{border-left:3px solid var(--acc);padding:6px 14px;background:var(--acc-soft);margin:14px 0;font-size:14px}} .note p{{margin:0;max-width:none}}
.note.warn{{border-left-color:var(--mid);background:var(--mid-soft)}}
.flow{{display:grid;gap:10px;margin-top:14px}} .fq{{display:grid;grid-template-columns:1fr auto 1fr;gap:10px}} @media (max-width:760px){{.fq{{grid-template-columns:1fr}}}}
.fbox{{background:var(--paper);border:1px solid var(--rule);padding:10px 14px;font-size:13.5px;line-height:1.6}} .fbox.q{{border-left:4px solid var(--acc)}}
.fbox.a1{{background:var(--c1-soft);border-color:var(--c1)}} .fbox.a2{{background:var(--c2-soft);border-color:var(--c2)}} .fbox.a0{{background:var(--ng-soft);border-color:var(--ng)}} .fbox.ab{{background:var(--acc-soft);border-color:var(--acc)}}
.fbox .k{{font-family:var(--mono);font-size:11px;letter-spacing:.08em;color:var(--ink3);display:block}} .arrow{{align-self:center;font-family:var(--mono);color:var(--ink3);font-size:12px;text-align:center}}
footer{{margin-top:60px;font-size:12px;color:var(--ink3);font-family:var(--mono);line-height:1.8}}
</style>
<div class="wrap">
<header>
  <div class="eyebrow">Graph Builder チャット ／ 多段QA 16問 ／ 前提：通常のハイブリッド検索で解けない問い</div>
  <h1>普通の検索で解けない問いに、グラフの 2 つの入口は何を上積みするか</h1>
  <p class="lede" style="margin-top:10px">まず 16 問を<b>グラフ無しのハイブリッド検索</b>（Graph Builder の fulltext モード：ベクトル索引＋全文索引、チャンク本文だけを LLM へ）で解いて基準線を引き、それで解けなかった問いだけを対象に、チャンク起点（既定モード）とエンティティ起点（entity_vector）がどこまで届いたかを比べました。基準線で解けた問いは、グラフの出番がありません。</p>
  <div class="tallies">
    <div class="tally base"><div class="k">基準線：ハイブリッド検索のみ（グラフ無し）</div><div class="v">{tally('base')}</div></div>
    <div class="tally"><div class="k">③ チャンク起点（リレーションに限定子）</div><div class="v">{tally('p3')}</div></div>
    <div class="tally"><div class="k">④ チャンク起点（規定ノード）</div><div class="v">{tally('p4')}</div></div>
    <div class="tally"><div class="k">④ エンティティ起点（entity_vector）</div><div class="v">{tally('p4ev')}</div></div>
  </div>
  <div class="gloss"><span class="gl">用語</span><dl>
    <dt>基準線</dt><dd>グラフを使わないハイブリッド検索。質問でチャンク索引（ベクトル＋全文）を引き、上位 4 チャンクの本文だけを LLM に渡す</dd>
    <dt>チャンク起点</dt><dd>既定モード。基準線と同じ 4 チャンクに加え、そのチャンクのエンティティから 1 ホップのトリプル（③はプロパティ付き、④は規定ノードの説明文）を文脈に足す</dd>
    <dt>エンティティ起点</dt><dd>entity_vector モード。質問でエンティティ索引を引き、上位 10 エンティティの説明文・トリプルと、それを含むチャンク（実測 1 枚）を文脈にする</dd>
    <dt>規定文</dt><dd>④で 1 規定ごとに作った Requirement ノードの説明文。例「潤滑油温度 ≤ 潤滑油入口温度+45°C（外部給油方式）」</dd>
  </dl></div>
</header>

<h2><span class="no">1</span>基準線で解けた {len(BASE_OK)} 問は、グラフの出番がない</h2>
<p>質問文に 2 つ目の文書の語（外部給油方式、V02313、インシュレーション…）が入っていて、全文索引がその文書のチャンクを直接当てる問いです。見かけは多段でも、普通の RAG で解けます。</p>
<ul class="plain">{base_ok_rows}</ul>
<div class="note warn"><p>グラフを足すと悪くなることもあります。M08 は基準線○ですが、④のチャンク起点では取り出しが大きくなって LLM が 6 点を拾えず△。エンティティ起点だけで動かすと M02・M04・M14 のように基準線で解けていた問いを落とします。<b>エンティティ起点は基準線の代わりではなく、上積み</b>として使う前提です。</p></div>

<h2><span class="no">2</span>残り {len(PREMISE)} 問で、何が救ったか</h2>
<p>ここが本題です。基準線で解けなかった {len(PREMISE)} 問のうち、いずれかのグラフの入口で○になったのは {len(solved_any)} 問。</p>
<div class="tbl"><table>
<thead><tr><th>ID</th><th>質問</th><th class="c">基準線</th><th class="c">③チャンク</th><th class="c">④チャンク</th><th class="c">④エンティティ</th><th>救ったもの</th><th>仕組み</th></tr></thead>
<tbody>{rows}</tbody>
<tfoot><tr><td colspan="2">○の数（{len(PREMISE)} 問中）</td><td class="c">{cnt('base', PREMISE)}</td><td class="c">{cnt('p3', PREMISE)}</td><td class="c">{cnt('p4', PREMISE)}</td><td class="c">{cnt('p4ev', PREMISE)}</td><td colspan="2">いずれかで○：{len(solved_any)}</td></tr></tfoot>
</table></div>

<h2><span class="no">3</span>2 つの入口が上積みするもの</h2>
<div class="two">
  <div class="origin chunk"><h3>チャンク起点<small>graph_vector_fulltext</small></h3>
    <div class="pipe">
      <div class="st"><b>入口</b><div class="v">基準線と同じ 4 チャンク</div></div>
      <div class="st"><b>上積み</b><div class="v hl">4 チャンクにぶら下がるエンティティから <b>1 ホップのトリプル</b>（1 エンティティ 19 本まで）。隣のチャンク本文は取らない</div></div>
      <div class="st"><b>届く答え</b><div class="v">トリプルの相手の名前かプロパティに<b>書いてある値</b>：参照先の図番、型式別の点数、判定値、責任者</div></div>
      <div class="st"><b>届かない</b><div class="v">当たったチャンクのエンティティから 2 段目にある値（③の判定）、19 本からこぼれた線、本文の段落そのもの</div></div>
    </div>
  </div>
  <div class="origin ent"><h3>エンティティ起点<small>entity_vector</small></h3>
    <div class="pipe">
      <div class="st"><b>入口</b><div class="v hl">質問 → エンティティ索引（名前＋規定文の埋め込み） → 上位 10</div></div>
      <div class="st"><b>上積み</b><div class="v hl">本文検索で当たらなかった箱に<b>規定文から到達</b>する。表の 1 行、別紙・表紙の欄</div></div>
      <div class="st"><b>届く答え</b><div class="v">質問とほぼ同義の規定文が抽出済みの値：条件付き判定値、責任部門</div></div>
      <div class="st"><b>届かない</b><div class="v">文書・型式の裏付け（チャンク 1 枚）、同じ項目の類似規定が複数文書にある場合の選別、規定文にない情報</div></div>
    </div>
  </div>
</div>

<h2><span class="no">4</span>前提の {len(PREMISE)} 問で言えること</h2>
<div class="cases">
  <div class="case c1"><h3>チャンク起点のトリプルが救う</h3><p class="cond">質問が名指しする文書のチャンクにぶら下がる項目・規格の<b>隣</b>に、答えが値として乗っている。</p>
    <ul><li>規格→規格の参照（図番）。ただし規格ノードの線が 19 本に入るか次第（③○ ④×）</li><li>型式別の点数など、計測項目のリレーションのプロパティ</li><li>④なら規定ノードの説明文が届けば値ごと来る</li></ul><span class="ex">M06（③） M09 M07（④） M10（④）</span></div>
  <div class="case c2"><h3>エンティティ起点が救う</h3><p class="cond">答えが<b>規定文として抽出済み</b>で、質問がその文とほぼ同義。しかも本文の置き場が表の 1 行や別紙の欄で、チャンク検索に当たらない。</p>
    <ul><li>条件付きの判定値（外部給油方式なら +45℃）</li><li>表紙・別紙にしかない主管部門</li><li>誰が承認・決裁するか</li></ul><span class="ex">M12 M13 M10</span></div>
  <div class="case c0"><h3>どちらも届かない</h3><p class="cond">答えが 2 文書目の<b>本文の段落</b>にしかない、3 文書以上の集約、同じ項目の類似規定の選び間違い。</p>
    <ul><li>定義文（Nt＝Nmax×0.8）はエンティティ起点で名前まで届いて△</li><li>3 文書×2 事実は 1 回の検索で揃わない</li><li>1% と ±2% のような類似規定は文脈に両方入ると LLM が誤る</li></ul><span class="ex">M03 M11 M15</span></div>
</div>
<div class="note"><p>言い切れる軸は 2 つです。<b>答えがトリプルの値として、質問の文書にぶら下がる項目の隣にあるか</b>（あればチャンク起点）。<b>答えが規定文として抽出済みで、質問と同義か</b>（そうならエンティティ起点）。どちらも「事前抽出でその値をグラフに書いてあるか」が前提で、書いてなければ普通の検索と同じ本文勝負になります。「文書を指定する語で本文が当たる」は基準線の強みであって、グラフの上積みではありません。</p></div>

<h2><span class="no">5</span>入口の選び方と、併用の形</h2>
<div class="flow">
  <div class="fq"><div class="fbox q"><span class="k">まず</span>基準線（ハイブリッド検索）で答えのチャンクが当たるか。当たれば終わり。当たるかは事前に分からないので、<b>常に基準線の 4 チャンクは持つ</b>。</div><div class="arrow">当たらない →</div><div class="fbox q"><span class="k">次に</span>答えは規定 1 件（条件＋値、参照先、責任者）として<b>抽出済みの種類</b>か？</div></div>
  <div class="fq"><div class="fbox a1"><span class="k">抽出済み ＋ 質問が文書を名指し</span>→ チャンク起点のトリプルで届く見込み。文書のチャンクから 1 ホップ。<span class="mono">M06 M07 M09</span></div><div class="arrow"></div><div class="fbox a2"><span class="k">抽出済み ＋ 質問が規定文と同義</span>→ エンティティ起点で直撃。<span class="mono">M12 M13</span></div></div>
  <div class="fq"><div class="fbox a0"><span class="k">抽出されていない（本文の段落・定義）</span>→ グラフでは届かない。基準線側の k を増やしリランカーで答えのチャンクを引き上げる。<span class="mono">M03</span></div><div class="arrow"></div><div class="fbox ab"><span class="k">併用の形</span>基準線 20 チャンク ∪ エンティティ起点 10 件のチャンク → リランカーで 6〜8 枚 → 残ったチャンクのエンティティから値を持つトリプルを優先して 1 ホップ → 1 つの文脈へ。</div></div>
</div>

<footer>データ: trace_base.json（fulltext モード）、trace_p3full.json、trace_p4full.json。すべて 2026-09-07 に同じ 16 問を実行。○△× は explain.json。③のグラフはエンティティ埋め込みが無く entity_vector は未測定。関連ページ: ③④比較（81ed0b59）、③図解（e833e29f）。</footer>
</div>
"""
open(f"{S}/origins.html", "w", encoding="utf-8").write(page)
print("origins.html", len(page), "chars; base_ok", BASE_OK, "premise", PREMISE, "solved_any", solved_any)
