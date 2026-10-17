# P1-a 後に残った 3 問を、「どんな文書があるか」→「答えはどこにあるか」→「検索はどこをたどったか」の順に図解 → p1a-failures.html
import json, re, html, unicodedata
from collections import Counter
S="/tmp/claude-1000/-home-uchida/4fdbd8d5-c462-43e6-88f0-fb9cf37aba38/scratchpad/std"
T={r["id"]:r for r in json.load(open(f"{S}/trace_p1a.json",encoding="utf-8"))}
CH=json.load(open(f"{S}/chunks_norm.json",encoding="utf-8"))
def e(s): return html.escape(str(s if s is not None else "").replace("�",""))
norm=lambda s: unicodedata.normalize("NFKC"," ".join(str(s).split()))
SH={"VTR-T_発行目録.pdf":"目録","HS-VTR-T001E.pdf":"T001","HS-VTR-T002F.pdf":"T002","VTR-T003D.pdf":"T003","VTR-T004C.pdf":"T004","VTR-T005B.pdf":"T005"}
FULL={"目録":"VTR-T_発行目録.pdf","T001":"HS-VTR-T001E.pdf","T002":"HS-VTR-T002F.pdf","T003":"VTR-T003D.pdf","T004":"VTR-T004C.pdf","T005":"VTR-T005B.pdf"}
NCH={SH[d]:n for d,n in Counter(c["doc"] for c in CH).items()}
def chunk(doc,pos):
    for c in CH:
        if SH[c["doc"]]==doc and c["pos"]==pos: return c["text"]
    return ""
DOCS=[
 ("目録","VTR-T_発行目録","HS-VTR 規格の発行目録（1 枚）","p1: 主管部門＝回転機事業部過給機部検査課、T001 の行 ／ p2: T002・T003・T004（RU・RH型初号機）の行 ／ p3〜5: 改訂符号の表","載っている規格 → T001・T002・T003・T004・T005"),
 ("T001","HS-VTR-T001E","過給機 単独試運転要領及び判定基準。全型式に共通の土台","p2: 空気吸込温度の計測点数（TPL.TPS 6点／VTR4 4点／VTR0.1・RU.RH.RH3・AT 2点） ／ p6: 外部振動の測定箇所 ／ p7: 2-9 タービン入口ガス温度 0〜-10% ／ p8-9: 計測項目・箇所・機器の表 ／ p10: 3-1 性能試験の判定（回転速度誤差 1%） ／ p14: 3-5 潤滑油供給条件（50kPa、10分前） ／ p15: 3-6 潤滑油温度（自己給油 130℃、外部給油 入口+45℃）","→ JIS B 8340、QC-KAI-012"),
 ("T002","HS-VTR-T002F","VTR4 シリーズ形過給機の初号機に関する コンプレッサ単独性能確認試験要領及び判定基準","p2: 初物仕様機＝QC-KAI-027 ／ p3: 試験方案 V00862 に基き性能計測 ／ p7: 3. 性能管理判定基準（サージライン ±3%、回転数一定曲線 ±2%、誤差 1%） ／ p8-9: 管理カーブ、検査部門長の決裁、4台目以降","→ V00862、QC-KAI-027、T001（性能試験）、T003（管理カーブ）"),
 ("T003","VTR-T003D","コンプレッサ性能管理曲線管理基準","p1: 管理曲線は検査部門が作成し検査部門長が承認 ／ p2: 年1回の見直し ／ p3: 関連基準の一覧（T001・T002・T004・T005） ／ p6: 別紙-1 主管部門＝回転機事業部過給機部検査課","関連基準 → T001・T002・T004・T005"),
 ("T004","VTR-T004C","RU.RH.RH3.AT 形過給機の初号機に関する コンプレッサ単独性能確認試験要領及び判定基準","p1: 適用、初物仕様機＝QC-KAI-027 ／ p2: 試験方案 V00862 に指示された回転速度 ／ p3: 規定チェック回転速度 Nt＝Nmax×0.8 ／ p4-5: n=2〜3台目、管理カーブ、検査部門長の決裁、4台目以降 ／ p14: 判定（±2%・±3%）","→ V00862、QC-KAI-027、T001（運転条件 3-5）、T003、T005"),
 ("T005","VTR-T005B","TPL.TPS 形過給機の初号機に関する コンプレッサ単独性能確認試験要領及び判定基準","p1: 適用、QC-KAI-027 ／ p2-3: 試験方案 V02313 ／ p4: 4台目以降 ／ p5: インシュレーション（TPS は取付ない） ／ p7: 3. 性能管理判定基準（±3%・±2%） ／ p8: 管理カーブ、検査部門長の決裁、4台目以降 ／ p9: 性能試験は T001 による","→ V02313、QC-KAI-027、T001、T003"),
]
doc_rows="".join(f"<tr><td class='mono'><b>{d}</b><br><span class='dim'>{e(f)}.pdf</span></td><td class='num'>{NCH.get(d,'?')}</td><td>{e(what)}</td><td class='small'>{e(where)}</td><td class='small'>{e(refs)}</td></tr>" for d,f,what,where,refs in DOCS)

# 3 問：答えの所在（文書・ページ）
ANS={
 "M06":{"q":T["M06"]["q"],"gold":"コンプレッサ性能計測標準試験方案 V00862","mark":"×",
        "path":[("目録 p2","「RU・RH型過給機の初号機に関する…」の行 → HS-VTR-T004 と分かる"),("T004 p2","『コンプレッサ性能計測標準試験方案:V00862』に指示された回転速度… ← 答え")],
        "keys":["V00862"]},
 "M09":{"q":T["M09"]["q"],"gold":"2点（相互差2℃以内）／4台目以降","mark":"△",
        "path":[("T001 p2","c. VTR0.1形及びRU.RH.RH3・AT形過給機 消音器フィルター外周2点で熱電対により計測 ← 答え1"),("T004 p5（同文 T005 p8）","管理カーブは検査部門長の決裁を得て配布し、4台目以降の運転機の判定基準とする ← 答え2")],
        "keys":["2点","4台目以降"]},
 "M11":{"q":T["M11"]["q"],"gold":"共通: QC-KAI-027／試験方案: T004=V00862、T005=V02313","mark":"△",
        "path":[("T002 p2・T004 p1・T005 p1","初物仕様機＝QC-KAI-027 初品管理規定 ← 答え1（3 文書に同じ文）"),("T002 p3・T004 p2","V00862 ← 答え2"),("T005 p2-3","V02313 ← 答え2")],
        "keys":["QC-KAI-027","V00862","V02313"]},
}
PICKS={  # backend ログ「rerank picked」（p1a 実行時）
 "M06":[("T002",3,0.067),("目録",2,0.051),("T005",2,0.042),("T004",2,0.023),("目録",1,0.022),("T004",7,0.021)],
 "M09":[("T005",8,0.761),("目録",2,0.370),("T001",12,0.248),("T004",1,0.222),("T003",2,0.206),("T004",5,0.187)],
 "M11":[("目録",2,0.336),("T003",3,0.142),("T001",11,0.107),("T004",1,0.085),("T003",2,0.076),("T005",1,0.060)],
}
WHY={
 "M06":("答えのページは 6 件に入り、本文も届いた。落ちたのは LLM の読み。決め手になる REFERENCES 行が予算外",
        "T004 p2（V00862 の行がある）はリランク 4 位で残り、本文として LLM に渡っている。しかし回答は「試験方案（図番）＝HS-VTR-T003／T004」で、文書番号を図番と読み違えた。以前③で正解したときは「VTR-T004 —REFERENCES→ V00862」という 1 行が文脈にあり、それが決め手だった。今回もその行は取り出しに 2 本あるが、Relationships 201 行のうちつなぎの行（SOURCE・APPLIES_TO）が先に詰まって入らなかった。なお T002 p3 も V00862 を持つが、こちらは VTR4 の要領なので質問の答えではない。",
        "質問に「図番・方案・規格」の語があれば REFERENCES 行を先に詰める。"),
 "M09":("答え1 のページ（T001 p2）が 6 件に入らなかった。取り出しの規定レコードにはあったが、説明文が予算外",
        "リランクが選んだのは T005 p8（管理カーブ・4台目以降＝答え2）、目録、T001 p12（配布先の表紙）、T004 p1、T003 p2、T004 p5。2 点を書いた T001 p2 は候補 20 件にはいたが上位 6 に入らなかった（質問の「計測点数」と、本文「消音器フィルター外周2点で熱電対により計測」の文面が遠い）。グラフ側では規定レコード REQ-VTR-T001-p2-5 の説明文「空気吸込温度 … RU.RH.RH3・AT形 @消音器フィルター外周 by 熱電対 2点」が取り出しに入っていたが、これは Entities 行で、予算は Relationships 207 行に使われて Entities は 2 行しか入らず、文脈に届いたのは「p2-5 SOURCE VTR-T001」という名前だけの行。4 台目以降は T005 p8 の本文で正答。",
        "質問の条件語（RU.RH.RH3.AT形、吸込温度）と一致する規定レコードの説明文を Relationships より先に詰める。P2 で値を MEASURED_AT {points: 2点} にも載せる。"),
 "M11":("答え1 のページは 2 本入って正答。答え2（図番）のページは 1 本も入らず、グラフの REFERENCES 行も予算外",
        "リランクが選んだのは 目録 p2、T003 p3、T001 p11、T004 p1（QC-KAI-027）、T003 p2、T005 p1（QC-KAI-027）。QC-KAI-027 は 2 本の本文で読めて正答。図番の V00862（T002 p3・T004 p2）と V02313（T005 p2-3）は 6 件に 1 本も入らず、取り出しには V02313 のエンティティ 3 件と REFERENCES 14 本があるが予算外。LLM は「試験方案の図番」を HS-VTR-T004／T005 という文書番号で埋めた。3 文書 × 2 事実を 1 回の検索で揃える問いで、チャンク 6 件では足りない。",
        "REFERENCES 行を先に詰めれば図番は届く見込み。3 文書の対応付け自体は追いかけ検索（P5）の領分。"),
}
REL=r" (ON|APPLIES_WHEN|JUDGED_BY|MEASURED_AT|MEASURED_BY|HAS_CRITERION|SOURCE|HAS_REQUIREMENT|REFERENCES|APPLIES_TO|PERFORMED_BY|DEFINED_IN) "
def comp(run):
    ctx=run["context"]; lines=ctx.split("\n")
    texts=re.findall(r"Text Content:\n(.*?)\n----\n",ctx,flags=re.S)
    rel=[l for l in lines if re.search(REL,l)]
    ent=[l for l in lines if re.match(r"^(Requirement|MeasurementItem|Condition|Standard|Criterion|Location|Instrument|Organization):",l) and not re.search(REL,l)]
    return {"chars":len(ctx),"texts":texts,"text_chars":sum(len(t) for t in texts),"rel":rel,"ent":ent,"rel_chars":sum(len(l)+1 for l in rel),"ent_chars":sum(len(l)+1 for l in ent),"types":Counter(re.search(REL,l).group(1) for l in rel)}
def where(run,c,key):
    k=norm(key); out=[]
    if any(k in norm(t) for t in c["texts"]): out.append(("本文にある","ok"))
    if any(k in norm(l) for l in c["rel"]): out.append(("Relationships 行にある","ok"))
    if any(k in norm(l) for l in c["ent"]): out.append(("Entities 行にある","ok"))
    me=[x for x in run["entities"] if k in norm(x["id"]+" "+(x.get("desc") or ""))]
    mr=[x for x in run["relationships"] if k in norm(x["s"]+" "+x["t"]+" "+x["o"]+" "+x.get("props",""))]
    if not out:
        out.append((f"取り出しにはある（エンティティ {len(me)}・リレーション {len(mr)}）が予算で落ちた","mid") if (me or mr) else ("取り出しにも無い","ng"))
    return out
cards=""; bars=""
for q in ("M06","M09","M11"):
    run=T[q]["runs"]["graph_vector_fulltext"]; c=comp(run); a=ANS[q]
    tot=c["chars"]; pt=c["text_chars"]*100/tot; pr=c["rel_chars"]*100/tot; pe=max(0.6,c["ent_chars"]*100/tot)
    bars+=f"<div class='bar'><span class='bl'>{q}</span><span class='w'><i class='t' style='width:{pt:.1f}%'></i><i class='r' style='width:{pr:.1f}%'></i><i class='e' style='width:{pe:.1f}%'></i></span><span class='bv'>本文 {c['text_chars']:,} 字（{len(c['texts'])} 文書行）／ Relationships {len(c['rel'])} 行 {c['rel_chars']:,} 字 ／ Entities {len(c['ent'])} 行</span></div>"
    path="".join(f"<li><b>{e(p)}</b>　{e(d)}</li>" for p,d in a["path"])
    picks=""
    for d,p,s in PICKS[q]:
        txt=chunk(d,p); hit=[k for k in a["keys"] if norm(k) in norm(txt)]
        picks+=f"<li class='{'key' if hit else ''}'><span class='sc'>{s:.3f}</span><b>{d} p{p}</b><span class='hd'>{e(txt[:44])}…</span>{(' <span class=hit>★ '+e('・'.join(hit))+'</span>') if hit else ''}</li>"
    wh="".join(f"<tr><td class='mono'>{e(k)}</td><td>{''.join(f'<span class=\"tag {cls}\">{e(w)}</span>' for w,cls in where(run,c,k))}</td></tr>" for k in a["keys"])
    title,why,fix=WHY[q]; ans=" ".join(run["answer"].split())
    cards+=f"""
<article class="card">
  <div class="ch"><span class="mark {'ng' if a['mark']=='×' else 'mid'}">{a['mark']}</span><span class="qid">{q}</span><h3>{e(a['q'])}</h3><div class="gold"><b>正解</b>　{e(a['gold'])}</div></div>
  <div class="sec"><div class="lbl">答えが書いてある場所（上の地図で言うと）</div><ul class="path">{path}</ul></div>
  <div class="grid">
    <div><div class="lbl">検索がたどった場所：リランクで残った 6 チャンク（スコア）。★は答えの語を含む</div><ol class="picks">{picks}</ol></div>
    <div><div class="lbl">答えの語は LLM に届いたか</div><table class="wh"><tbody>{wh}</tbody></table>
      <div class="lbl" style="margin-top:8px">文脈 {c['chars']:,} 字に入った Relationships の種類</div><div class="small">{e('、'.join(f'{t} {n}' for t,n in c['types'].most_common(6)))}</div></div>
  </div>
  <div class="sec"><div class="lbl">回答</div><div class="ans">{e(ans[:400])}{'…' if len(ans)>400 else ''}</div></div>
  <div class="why"><b>{e(title)}</b><p>{e(why)}</p><p class="fix"><b>P1-b で直す手：</b>{e(fix)}</p></div>
</article>"""
page=f"""<title>P1-a 後に残った 3 問</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=BIZ+UDPGothic:wght@400;700&family=Noto+Sans+JP:wght@400;500;700&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
:root{{--bg:#F3F5F7;--paper:#FFFFFF;--ink:#1B2430;--ink2:#4A5665;--ink3:#7A8794;--rule:#CBD3DC;--rule2:#E3E8EE;--acc:#1F4E79;--acc-soft:#E4EDF6;--ok:#2E7D4F;--ok-soft:#E3F1E8;--mid:#A86A12;--mid-soft:#F6ECD9;--ng:#B3312F;--ng-soft:#F7E3E2;--ans:#F4F6F8;--ent:#EEF3F8;
 --mono:"IBM Plex Mono","Noto Sans JP",ui-monospace,Menlo,monospace;--head:"BIZ UDPGothic","Noto Sans JP","Hiragino Sans","Yu Gothic",sans-serif;--body:"Noto Sans JP","Hiragino Sans","Yu Gothic",system-ui,sans-serif}}
@media (prefers-color-scheme: dark){{:root:not([data-theme="light"]){{--bg:#12181F;--paper:#1A222C;--ink:#E4E9EF;--ink2:#AEB9C5;--ink3:#7E8A97;--rule:#37424E;--rule2:#28323D;--acc:#8BBBE8;--acc-soft:#1E2E40;--ok:#6FCB93;--ok-soft:#173225;--mid:#E0A94A;--mid-soft:#3A2C12;--ng:#F08A86;--ng-soft:#3F1F1F;--ans:#202832;--ent:#1E2733}}}}
:root[data-theme="dark"]{{--bg:#12181F;--paper:#1A222C;--ink:#E4E9EF;--ink2:#AEB9C5;--ink3:#7E8A97;--rule:#37424E;--rule2:#28323D;--acc:#8BBBE8;--acc-soft:#1E2E40;--ok:#6FCB93;--ok-soft:#173225;--mid:#E0A94A;--mid-soft:#3A2C12;--ng:#F08A86;--ng-soft:#3F1F1F;--ans:#202832;--ent:#1E2733}}
*{{box-sizing:border-box}} body{{background:var(--bg);color:var(--ink);font-family:var(--body);font-size:15px;line-height:1.75;margin:0}}
.wrap{{max-width:1060px;margin:0 auto;padding:36px 24px 80px}}
h1,h2,h3{{font-family:var(--head);text-wrap:balance;margin:0}} h1{{font-size:28px;line-height:1.3}}
h2{{font-size:21px;margin:48px 0 14px;padding-bottom:8px;border-bottom:2px solid var(--acc);display:flex;align-items:baseline;gap:12px}} h2 .no{{font-family:var(--mono);font-size:13px;color:var(--acc)}}
h3{{font-size:16px;line-height:1.5}} p{{margin:0 0 12px;max-width:72ch}} .lede{{font-size:16px;color:var(--ink2);max-width:70ch}}
.eyebrow{{font-family:var(--mono);font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:var(--acc);margin-bottom:8px}}
.mono{{font-family:var(--mono);font-size:12.5px}} .small{{font-size:12.5px;color:var(--ink2)}} .dim{{color:var(--ink3);font-size:11px}}
.mark{{display:inline-flex;align-items:center;justify-content:center;width:26px;height:26px;border-radius:50%;border:2px solid currentColor;font-family:var(--head);font-weight:700;font-size:15px;line-height:1;flex:none}}
.mark.mid{{color:var(--mid);background:var(--mid-soft)}} .mark.ng{{color:var(--ng);background:var(--ng-soft)}}
table{{border-collapse:collapse;width:100%;background:var(--paper);font-size:13.5px}} .tbl{{overflow-x:auto;border:1px solid var(--rule)}} .tbl table{{min-width:760px}}
th,td{{padding:8px 10px;border-bottom:1px solid var(--rule2);text-align:left;vertical-align:top}} th{{font-family:var(--head);font-size:12.5px;color:var(--ink2);background:var(--ans);white-space:nowrap}} tbody tr:last-child td{{border-bottom:none}} td.num{{text-align:right;font-family:var(--mono)}}
.note{{border-left:3px solid var(--acc);padding:6px 14px;background:var(--acc-soft);margin:14px 0;font-size:14px}} .note p{{margin:0;max-width:none}}
/* 参照の図 */
.refmap{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin:12px 0}} @media (max-width:800px){{.refmap{{grid-template-columns:1fr}}}}
.rbox{{background:var(--paper);border:1px solid var(--rule);padding:10px 12px;font-size:13px;min-width:0}} .rbox b{{display:block;font-family:var(--head);font-size:14px;margin-bottom:4px}} .rbox .ar{{color:var(--ink3);font-family:var(--mono);font-size:12px}}
.rbox .doc{{display:inline-block;border:1.5px solid var(--acc);background:var(--ent);border-radius:3px;padding:1px 7px;font-family:var(--mono);font-size:12px;margin:2px 2px}} .rbox .ext{{display:inline-block;border:1.5px dashed var(--mid);border-radius:3px;padding:1px 7px;font-family:var(--mono);font-size:12px;margin:2px 2px}}
/* バー */
.bars{{display:grid;gap:8px;margin:12px 0}} .bar{{display:grid;grid-template-columns:44px 1fr;gap:10px;align-items:center}} .bar .bl{{font-family:var(--mono);font-size:12px;color:var(--acc)}}
.bar .w{{display:flex;height:18px;border-radius:2px;overflow:hidden;background:var(--rule2)}} .bar .w i{{display:block;height:100%}} .bar .w i.t{{background:var(--ok)}} .bar .w i.r{{background:var(--acc)}} .bar .w i.e{{background:var(--mid)}}
.bar .bv{{grid-column:2;font-size:12.5px;color:var(--ink2);margin-top:-4px}}
.legend{{display:flex;gap:16px;font-size:12.5px;color:var(--ink2);flex-wrap:wrap}} .legend span::before{{content:"";display:inline-block;width:10px;height:10px;margin-right:6px;border-radius:2px;vertical-align:middle}} .legend .t::before{{background:var(--ok)}} .legend .r::before{{background:var(--acc)}} .legend .e::before{{background:var(--mid)}}
/* カード */
.card{{background:var(--paper);border:1px solid var(--rule);margin:18px 0}}
.ch{{padding:14px 20px 12px;border-bottom:1px solid var(--rule2);display:grid;grid-template-columns:auto auto 1fr;gap:6px 12px;align-items:baseline}} .ch h3{{grid-column:1/-1}} .qid{{font-family:var(--mono);font-size:13px;color:var(--acc)}}
.gold{{grid-column:1/-1;font-size:13.5px;color:var(--ink2)}} .gold b{{color:var(--ok)}}
.sec{{padding:10px 20px 0}} .grid{{display:grid;grid-template-columns:1fr 1fr;gap:14px;padding:10px 20px}} @media (max-width:760px){{.grid{{grid-template-columns:1fr}}}} .grid>div{{min-width:0}}
.lbl{{font-family:var(--mono);font-size:11px;letter-spacing:.08em;color:var(--ink3);text-transform:uppercase;margin:6px 0 4px}}
ul.path{{margin:0;padding:0;list-style:none;display:grid;gap:3px}} ul.path li{{background:var(--ok-soft);padding:5px 10px;border-radius:2px;font-size:13.5px}} ul.path li b{{font-family:var(--mono);font-weight:500;color:var(--ok)}}
ol.picks{{margin:0;padding:0;list-style:none;display:grid;gap:3px;counter-reset:n}} ol.picks li{{display:grid;grid-template-columns:18px 54px 74px 1fr;gap:8px;align-items:baseline;font-size:13px;padding:4px 6px;background:var(--ans);border-radius:2px;counter-increment:n;min-width:0}}
ol.picks li::before{{content:counter(n);font-family:var(--mono);font-size:11px;color:var(--ink3)}} ol.picks li .sc{{font-family:var(--mono);font-size:12px;color:var(--acc)}} ol.picks li .hd{{color:var(--ink3);font-size:11.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}} ol.picks li.key{{outline:1.5px solid var(--ok)}} .hit{{color:var(--ok);font-size:12px;white-space:nowrap}}
table.wh td:first-child{{width:120px}} .tag{{display:inline-block;font-size:12px;padding:1px 8px;border-radius:10px;margin:1px 4px 1px 0;border:1px solid currentColor}} .tag.ok{{color:var(--ok);background:var(--ok-soft)}} .tag.mid{{color:var(--mid);background:var(--mid-soft)}} .tag.ng{{color:var(--ng);background:var(--ng-soft)}}
.ans{{background:var(--ans);padding:8px 12px;font-size:13.5px;line-height:1.65;border-left:3px solid var(--rule)}}
.why{{padding:12px 20px 16px;font-size:14px}} .why p{{margin:6px 0 0;max-width:none}} .why .fix{{color:var(--acc)}}
footer{{margin-top:60px;font-size:12px;color:var(--ink3);font-family:var(--mono);line-height:1.8}}
</style>
<div class="wrap">
<header>
  <div class="eyebrow">Graph Builder フォーク ／ P1-a（候補 20 → リランク 6、断片選別なし、予算 14,000 字）／ 残った 3 問</div>
  <h1>P1-a で○にならなかった 3 問は、どの文書のどこに答えがあり、検索はどこを見ていたか</h1>
  <p class="lede" style="margin-top:10px">まず対象の文書 6 本が何で、互いにどう参照し合っているかを地図にし、その上に「答えのページ」と「リランクが選んだページ」を重ねます。3 問の落ち方はそれで見えます。</p>
</header>

<h2><span class="no">1</span>文書は 6 本、チャンク 86 個</h2>
<div class="tbl"><table>
<thead><tr><th>略称</th><th>チャンク</th><th>何の文書か</th><th>主な中身（ページ位置）</th><th>他文書への参照</th></tr></thead>
<tbody>{doc_rows}</tbody></table></div>
<div class="refmap">
  <div class="rbox"><b>目録 が載せる規格</b><span class="doc">T001</span><span class="doc">T002</span><span class="doc">T003</span><span class="doc">T004</span><span class="doc">T005</span><div class="ar">↑ 型式名から規格番号を引く入口。「RU・RH型初号機」→ T004</div></div>
  <div class="rbox"><b>T003 の関連基準</b><span class="doc">T001</span><span class="doc">T002</span><span class="doc">T004</span><span class="doc">T005</span><div class="ar">↑ 管理曲線基準が下に置く 4 本。3 ホップ問題（M07）の橋</div></div>
  <div class="rbox"><b>初号機要領 3 本が引く外部文書</b><span class="doc">T002</span><span class="doc">T004</span><span class="ar">→</span><span class="ext">V00862（試験方案）</span><br><span class="doc">T005</span><span class="ar">→</span><span class="ext">V02313（試験方案）</span><br><span class="doc">T002</span><span class="doc">T004</span><span class="doc">T005</span><span class="ar">→</span><span class="ext">QC-KAI-027（初品管理）</span><div class="ar">＋ 3 本とも運転条件・性能試験は T001 に依る</div></div>
</div>
<div class="note"><p>読み方の要点は 3 つ。<b>T001 は全型式共通の土台</b>で、点数・温度・圧力の具体値はここに集まっている（M09 の 2 点、M12 の +45℃、M07 の 0〜-10%）。<b>T002・T004・T005 は型式別の初号機要領</b>で、同じ構成（適用→計測→判定→管理カーブ）を持ち、図番と初品管理規定を引く。<b>目録と T003 は索引</b>で、本文の答えは持たない。</p></div>

<h2><span class="no">2</span>3 問それぞれ：答えの場所 → 検索が見た場所 → 届いたか → 回答</h2>
{cards}

<h2><span class="no">3</span>3 問に共通する原因：予算 14,000 字のほとんどが「つなぎの行」（SOURCE・APPLIES_TO）</h2>
<div class="legend"><span class="t">本文（Text Content）</span><span class="r">Relationships 行</span><span class="e">Entities 行（規定レコードの説明文はここ）</span></div>
<div class="bars">{bars}</div>
<div class="note"><p>P1-a の詰め方は「本文 → Relationships → Entities」で、Relationships は Neo4j が返した順のまま。M09 の文脈の Relationships 207 行の内訳は、SOURCE 94（「この規定レコードは T003 に書いてある」）、APPLIES_TO 72（「この項目は TPL.TPS 形に関する」）、JUDGED_BY 25、MEASURED_AT／MEASURED_BY 8、その他 8。<b>SOURCE と APPLIES_TO はノード同士のつながりだけを示し、答えになる値を運ばない</b>（ここではこれを「つなぎの行」と呼ぶ）。この 2 種類で 207 行中 166 行、1 万字以上を占め、値を運べる行（規定レコードの説明文、REFERENCES {context: …図番…}）は 2 行しか入らなかった。④の射影リレーションにはプロパティが無く、207 行のうち {…} 付きは 0 本。本文は全体の 1 割前後。④は値を説明文に持つ表現なので、この順番とは相性が最悪だった。</p></div>

<h2><span class="no">4</span>P1-b に持ち込むこと</h2>
<div class="tbl"><table>
<thead><tr><th>問い</th><th>落ちた場所</th><th>直す手</th></tr></thead>
<tbody>
<tr><td>M06</td><td>答えのページは届いたが、LLM が図番と文書番号を取り違えた。決め手の REFERENCES 行が予算外</td><td>質問に「図番・方案・規格」の語があれば REFERENCES 行を先に詰める</td></tr>
<tr><td>M09</td><td>答え1 のページ（T001 p2）がリランクの 6 件に入らず、規定レコードの説明文も予算で切れた</td><td>質問の条件語と一致する規定レコードの説明文を Relationships より先に。P2 で値をリレーションのプロパティにも載せる</td></tr>
<tr><td>M11</td><td>図番のページが 1 本も入らず、REFERENCES 14 本も予算外。3 文書の対応付けが要る</td><td>REFERENCES 優先で図番は届く。3 文書の対応付けは P5 の追いかけ検索</td></tr>
</tbody></table></div>
<p style="margin-top:12px">予算の配り方はこうします。本文は全部。次に、質問の条件語（型式・給油方式・部門名）や図番の語と一致する行を、Relationships・Entities の区別なく先に。次に値を持つ種類（JUDGED_BY・MEASURED_AT・REFERENCES・規定レコードの説明文）。SOURCE・APPLIES_TO のようなつなぎの行（値を運ばない行）は最後。並べ替えは 1 ホップ展開の Cypher 側（条件語の一致数）と、詰める段の両方でやります。</p>

<footer>データ: trace_p1a.json（2026-09-13、④グラフ、既定モード＋リランク）、backend ログの「rerank picked」、Neo4j の Chunk 86 個（NFKC 正規化して語を照合）。関連: 改造計画（ac2c59aa）、条件を見て枝を選ぶ展開クエリ（a2ce3eb9）。</footer>
</div>
"""
open(f"{S}/p1a-failures.html","w",encoding="utf-8").write(page)
o=len(re.findall(r"<div\b",page)); c=page.count("</div>"); print("written",len(page),"div",o,c,"FFFD",page.count("�"))
