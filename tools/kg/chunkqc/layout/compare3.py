# 語の被覆: 生テキストの語（3 字以上の連続する文字）のうち、変換結果に含まれる割合。並び替えや表の組み直しに影響されない。
import glob, os, re, unicodedata, json, collections, statistics
import fitz
def norm(s): return re.sub(r"\s+","",unicodedata.normalize("NFKC",s or ""))
WORD=re.compile(r"[^\W_]{3,}")
chunks=json.load(open("/work/chunk_check.json",encoding="utf-8"))["chunks"]
res=collections.defaultdict(lambda: {"d":[], "c":[]}); lost=collections.Counter()
for mdp in sorted(glob.glob("/work/docling_md/*.md")):
    base=os.path.basename(mdp)[:-3]; d,name=base.split("__",1); pdf=f"/docs/{d}/{name}.pdf"
    try:
        with fitz.open(pdf) as doc: raw="".join(p.get_text() for p in doc)
    except Exception: continue
    rawn=norm(raw); words=set(WORD.findall(rawn))
    if len(words)<30: continue
    md=re.sub(r"!\[Image\]\([^)]*\)","",open(mdp,encoding="utf-8",errors="replace").read()); mdn=norm(md)
    cov_d=sum(1 for w in words if w in mdn)/len(words)
    key=name+".pdf"; cur=chunks.get(key) or chunks.get(key.replace("VTR-T003D","VTR-T003 D"))
    if cur:
        curn=norm("".join(re.sub(r"^\[[^\]]*\]\n","",c["text"],count=1) for c in cur)); cov_c=sum(1 for w in words if w in curn)/len(words); res[d]["c"].append(cov_c)
    res[d]["d"].append(cov_d)
    for w in words:
        if w not in mdn and len(w)>=5: lost[w]+=1
print("フォルダ   語の被覆 Docling / 現状の経路（中央値、最小）")
for d,r in res.items():
    print(f"{d:8s} {100*statistics.median(r['d']):5.0f}%（最小 {100*min(r['d']):.0f}%） / {100*statistics.median(r['c']):5.0f}%（最小 {100*min(r['c']):.0f}%）")
print("Docling で欠けた語の例（5 字以上、出現文書数）:", lost.most_common(24))
