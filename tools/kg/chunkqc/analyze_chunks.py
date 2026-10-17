# chunk_check.json（現状の経路のチャンク）を読み、枠の混入（独立行）、表の混線、本文の行の残存率を数える。読むだけ。
import json, re, glob, os, unicodedata, collections, statistics
import fitz
D=json.load(open("/work/chunk_check.json",encoding="utf-8")); chunks=D["chunks"]
def norm(s): return re.sub(r"\s+","",unicodedata.normalize("NFKC",s or ""))
FRAME_LINE=re.compile(r"^\s*(配布先|承認|審査|作成|控|原|合計|番|号|改正回数|改正番号\s*\d*|来\s?歴|年\s?月\s?日|HS|回[汎管品生製技][^\s]{0,4}\s*\d?|'?\d{2}\.\d{2}\.\d{2}|\d{4}/\d{1,2}/\d{1,2}|[A-E])\s*$")
def category(folder,name):
    if folder=="VTRT": return "目録" if "目録" in name else "試運転・検査"
    if "目録" in name: return "目録"
    m=re.search(r"HS-(RU|TP)-?0?([0-9１]{2,3})",name)
    if m:
        n=int(unicodedata.normalize("NFKC",m.group(2)))
        if m.group(1)=="RU": return "加工・処理" if n in (11,14,15,16) else "素材・部品"
        if n in (2,6,7,16,17,25): return "加工・処理"
        if n in (21,23): return "受検・証明"
        return "素材・部品"
    if "VTR-P" in name or "廃止" in name or "V02673" in name: return "目録・通知・その他"
    return "報告書"
paths={os.path.basename(p):p for d in ("RHAT","TPS","VTRT") for p in glob.glob(f"/docs/{d}/*.pdf")}
paths.update({os.path.basename(p):p for p in glob.glob("/docs/試運転要領/*.pdf")})
rows=[]; ex={"frame":[], "table":[], "tiny":[], "good":[]}
for r in D["rows"]:
    if "chunks" not in r: continue
    name=r["file"]; cs=chunks[name]; cat=category(r["folder"],name)
    p=paths.get(name) or paths.get(name.replace("VTR-T003D","VTR-T003 D"))
    raw=""; 
    if p:
        with fitz.open(p) as doc: raw="".join(pg.get_text() for pg in doc)
    lines=[norm(l) for l in raw.splitlines() if len(norm(l))>=6]
    alltext=norm("".join(re.sub(r"^\[[^\]]*\]\n","",c["text"],count=1) for c in cs))
    cover=sum(1 for l in lines if l in alltext)/max(len(lines),1)
    nf=0; nt=0; ntf=0; tiny=0
    for c in cs:
        body=re.sub(r"^\[[^\]]*\]\n","",c["text"],count=1)
        fl=sum(1 for l in body.splitlines() if FRAME_LINE.match(l))
        if fl>=2: nf+=1; 
        if fl>=2 and len(ex["frame"])<3 and "VTR" in name: ex["frame"].append((name,c["section"],body[:420]))
        if "|---" in body:
            nt+=1
            if re.search(r"\|[^\n]*(承認|審査|来\s?歴|改正回数|配布先)[^\n]*\|",body): ntf+=1; 
            if ntf and len(ex["table"])<2 and re.search(r"\|[^\n]*(承認|審査|来\s?歴|改正回数)[^\n]*\|",body): ex["table"].append((name,c["section"],body[:420]))
        if c["tokens"]<80:
            tiny+=1
            if len(ex["tiny"])<3: ex["tiny"].append((name,c["section"],body[:160]))
        elif 200<=c["tokens"]<=512 and fl==0 and len(ex["good"])<1 and "RU-002" in name: ex["good"].append((name,c["section"],body[:420]))
    rows.append({"cat":cat,"file":name,"chunks":len(cs),"frame":nf,"tables":nt,"tables_with_frame":ntf,"tiny":tiny,"sections":r["sections"],"heads":r["headings_in_text"],"cover":cover})
agg=collections.defaultdict(collections.Counter); cov=collections.defaultdict(list)
for x in rows:
    a=agg[x["cat"]]; a["files"]+=1; a["chunks"]+=x["chunks"]; a["frame"]+=x["frame"]; a["tables"]+=x["tables"]; a["tf"]+=x["tables_with_frame"]; a["tiny"]+=x["tiny"]; a["sec"]+=x["sections"]; a["heads"]+=x["heads"]; cov[x["cat"]].append(x["cover"])
print(f"{'種類':12s} 文書 チャンク 枠の独立行が2以上 表 うち枠混線 細切れ 節/見出し 行の残存(中央値)")
for k,a in agg.items():
    print(f"{k:12s} {a['files']:3d} {a['chunks']:5d} {a['frame']:4d}({100*a['frame']/a['chunks']:.0f}%) {a['tables']:3d} {a['tf']:3d} {a['tiny']:3d}({100*a['tiny']/a['chunks']:.0f}%) {a['sec']:3d}/{a['heads']:<3d} {100*statistics.median(cov[k]):.0f}%")
tot=collections.Counter()
for a in agg.values():
    for k,v in a.items(): tot[k]+=v
print(f"{'全体':12s} {tot['files']:3d} {tot['chunks']:5d} {tot['frame']:4d}({100*tot['frame']/tot['chunks']:.0f}%) {tot['tables']:3d} {tot['tf']:3d} {tot['tiny']:3d}({100*tot['tiny']/tot['chunks']:.0f}%) {tot['sec']:3d}/{tot['heads']:<3d} {100*statistics.median([c for v in cov.values() for c in v]):.0f}%")
low=[x for x in rows if x["cover"]<0.85]; print("行の残存が 85% 未満:", [(x["file"][:22],round(x["cover"],2)) for x in low][:12])
json.dump({"rows":rows,"examples":ex},open("/work/analysis.json","w",encoding="utf-8"),ensure_ascii=False,indent=1)
for k,v in ex.items():
    for name,sec,body in v: print(f"\n### 例 {k}: {name} [{sec}]\n{body}")
