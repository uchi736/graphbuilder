# Docling の Markdown（doc-parser /docling/parse）を、現状の経路と同じ物差しで数える。
# 行の残存（PyMuPDF の生テキストの行がどれだけ残るか）、枠の語（独立行＝後で外せる／本文行に癒着＝外せない）、
# 見出し（## の数と、本文の番号見出しの数）、表（Markdown 表の数と、枠の語が混ざる表）。
import glob, os, re, unicodedata, json, collections, statistics
import fitz
def norm(s): return re.sub(r"\s+","",unicodedata.normalize("NFKC",s or ""))
FRAME=r"(配布先|承\s?認|審\s?査|作\s?成|控|原|合\s?計|番|号|改正回数|改正番号\s*\d*|来\s?歴|年\s?月\s?日|HS|回[汎管品生製技][^\s]{0,4}|技生技|'?\d{2}[./]\d{2}[./]\d{2}|\d{4}/\d{1,2}/\d{1,2}|[A-E])"
FRAME_LINE=re.compile(r"^\s*"+FRAME+r"\s*\d?\s*$")
GLUED=re.compile(r"^\s*"+FRAME+r"\s+\d?\s*\S{6,}")
HEAD=re.compile(r"^\s*(\d{1,2}[.．](?!\d)\s*\S{1,30}|\d{1,2}[-－]\d{1,2}\s*\S{0,30}|別表\s*\d+.{0,20}|注\s*\d*.{0,20})\s*$")
def category(d,name):
    if d=="試運転要領": return "目録" if "目録" in name else "試運転・検査"
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
rows=[]
for mdp in sorted(glob.glob("/work/docling_md/*.md")):
    base=os.path.basename(mdp)[:-3]; d,name=base.split("__",1); pdf=f"/docs/{d}/{name}.pdf"
    md=open(mdp,encoding="utf-8",errors="replace").read()
    if md.startswith("{") and "detail" in md[:200]: rows.append({"cat":category(d,name),"file":name,"error":md[:80]}); continue
    md=re.sub(r"!\[Image\]\([^)]*\)","",md)
    try:
        with fitz.open(pdf) as doc: raw="".join(p.get_text() for p in doc)
    except Exception as e: rows.append({"cat":category(d,name),"file":name,"error":str(e)}); continue
    lines=[norm(l) for l in raw.splitlines() if len(norm(l))>=6]
    mdn=norm(md); cover=sum(1 for l in lines if l in mdn)/max(len(lines),1)
    mls=[l for l in md.splitlines() if l.strip()]
    frame_alone=sum(1 for l in mls if FRAME_LINE.match(l)); glued=sum(1 for l in mls if GLUED.match(l) and not l.startswith("|"))
    heads_md=sum(1 for l in mls if re.match(r"^#{1,6}\s",l)); heads_raw=sum(1 for l in raw.splitlines() if HEAD.match(l.strip()))
    # 表: 連続する | 行のブロック
    tables=0; tframe=0; inblk=False; blk=[]
    for l in mls+["END"]:
        if l.startswith("|"): blk.append(l); inblk=True
        else:
            if inblk:
                tables+=1
                if re.search(r"\|[^\n|]*(承\s?認|審\s?査|来\s?歴|改正回数|配布先)[^\n|]*\|","\n".join(blk)): tframe+=1
            inblk=False; blk=[]
    rows.append({"cat":category(d,name),"file":name,"cover":cover,"frame_alone":frame_alone,"glued":glued,"heads_md":heads_md,"heads_raw":heads_raw,"tables":tables,"tframe":tframe,"chars":len(mdn),"raw_chars":len(norm(raw))})
agg=collections.defaultdict(collections.Counter); cov=collections.defaultdict(list)
for r in rows:
    if "error" in r: print("ERR",r["file"][:30],r["error"][:60]); continue
    a=agg[r["cat"]]; a["files"]+=1; a["alone"]+=r["frame_alone"]; a["glued"]+=r["glued"]; a["hmd"]+=r["heads_md"]; a["hraw"]+=r["heads_raw"]; a["tables"]+=r["tables"]; a["tframe"]+=r["tframe"]; cov[r["cat"]].append(r["cover"])
print(f"{'種類':12s} 文書 行の残存(中央値) 枠の独立行 枠の癒着行 ##見出し/番号見出し 表 うち枠混線")
tot=collections.Counter(); allcov=[]
for k,a in agg.items():
    print(f"{k:12s} {a['files']:3d} {100*statistics.median(cov[k]):5.0f}% {a['alone']:6d} {a['glued']:6d} {a['hmd']:4d}/{a['hraw']:<4d} {a['tables']:3d} {a['tframe']:3d}")
    for kk,v in a.items(): tot[kk]+=v
    allcov+=cov[k]
print(f"{'全体':12s} {tot['files']:3d} {100*statistics.median(allcov):5.0f}% {tot['alone']:6d} {tot['glued']:6d} {tot['hmd']:4d}/{tot['hraw']:<4d} {tot['tables']:3d} {tot['tframe']:3d}")
low=[(r["file"][:24],round(r["cover"],2)) for r in rows if "error" not in r and r["cover"]<0.9]; print("残存 90% 未満:",low[:15])
json.dump(rows,open("/work/docling_analysis.json","w",encoding="utf-8"),ensure_ascii=False,indent=1)
