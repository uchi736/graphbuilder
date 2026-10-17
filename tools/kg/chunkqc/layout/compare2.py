# Docling と現状の経路を、文字の残存（10 字の窓の被覆）と表の区分（枠だけの表／本文の表に枠が混ざる／きれい）で比べる。
import glob, os, re, unicodedata, json, collections, statistics
import fitz
def norm(s): return re.sub(r"\s+","",unicodedata.normalize("NFKC",s or ""))
FRAME=re.compile(r"^(配布先|承\s?認|審\s?査|作\s?成|控|原|合\s?計|番|号|改正回数|改正番号\s*\d*|来\s?歴|年\s?月\s?日|HS|回[汎管品生製技].{0,4}|技生技|'?\d{2}[./]\d{2}[./]\d{2}|\d{4}/\d{1,2}/\d{1,2}|[A-E]|[－―—-]|\d{1,2})$")
def shingles(t,k=10): return {t[i:i+k] for i in range(0,max(len(t)-k+1,0),3)}
def table_blocks(lines):
    blocks=[]; blk=[]
    for l in lines+["END"]:
        if l.startswith("|"): blk.append(l)
        else:
            if blk: blocks.append(blk); blk=[]
    return blocks
def classify(blk):
    cells=[c.strip() for l in blk if not re.match(r"^\|[\s:|-]+\|$",l) for c in l.strip("|").split("|")]
    cells=[norm(c) for c in cells]; non=[c for c in cells if c]
    if not non: return "空"
    fr=sum(1 for c in non if FRAME.match(c))
    if fr/len(non)>=0.5: return "枠だけ"
    if fr>0 or any(re.search(r"承認|審査|来歴|改正回数|配布先",c) for c in non): return "本文の表に枠"
    return "きれい"
chunks=json.load(open("/work/chunk_check.json",encoding="utf-8"))["chunks"] if os.path.exists("/work/chunk_check.json") else {}
res=collections.defaultdict(lambda: collections.defaultdict(list))
missing_examples={}
for mdp in sorted(glob.glob("/work/docling_md/*.md")):
    base=os.path.basename(mdp)[:-3]; d,name=base.split("__",1); pdf=f"/docs/{d}/{name}.pdf"
    try:
        with fitz.open(pdf) as doc: raw="".join(p.get_text() for p in doc)
    except Exception: continue
    rawn=norm(raw); S=shingles(rawn)
    if len(S)<20: continue
    md=re.sub(r"!\[Image\]\([^)]*\)","",open(mdp,encoding="utf-8",errors="replace").read()); mdn=norm(md)
    cov_d=sum(1 for s in S if s in mdn)/len(S)
    key=name+".pdf"; cur=chunks.get(key) or chunks.get(key.replace("VTR-T003D","VTR-T003 D"))
    cov_c=None
    if cur:
        curn=norm("".join(re.sub(r"^\[[^\]]*\]\n","",c["text"],count=1) for c in cur)); cov_c=sum(1 for s in S if s in curn)/len(S)
    tb_d=collections.Counter(classify(b) for b in table_blocks([l for l in md.splitlines() if l.strip()]))
    tb_c=collections.Counter()
    if cur:
        for c in cur: tb_c.update(classify(b) for b in table_blocks([l for l in c["text"].splitlines() if l.strip()]))
    res[d]["cov_d"].append(cov_d); 
    if cov_c is not None: res[d]["cov_c"].append(cov_c)
    for k,v in tb_d.items(): res[d]["td_"+k].append(v)
    for k,v in tb_c.items(): res[d]["tc_"+k].append(v)
    if name.startswith(("HS-RU-013","HS-RU-011")):
        miss=[l for l in [norm(x) for x in raw.splitlines() if len(norm(x))>=8] if l not in mdn][:8]; missing_examples[name]=miss
print("フォルダ  Docling 被覆(中央値)  現状 被覆(中央値) | Docling の表: 枠だけ/本文に枠/きれい | 現状の表: 枠だけ/本文に枠/きれい")
for d,r in res.items():
    f=lambda k: sum(r.get(k,[]))
    print(f"{d:8s} {100*statistics.median(r['cov_d']):5.0f}% {100*statistics.median(r['cov_c']) if r.get('cov_c') else 0:5.0f}% | {f('td_枠だけ'):3d}/{f('td_本文の表に枠'):3d}/{f('td_きれい'):3d} | {f('tc_枠だけ'):3d}/{f('tc_本文の表に枠'):3d}/{f('tc_きれい'):3d}")
allc=[x for r in res.values() for x in r['cov_d']]; allk=[x for r in res.values() for x in r.get('cov_c',[])]
print(f"全体     {100*statistics.median(allc):5.0f}% {100*statistics.median(allk):5.0f}%")
for n,m in missing_examples.items(): print("Docling で欠けた行の例", n, ":", m[:6])
