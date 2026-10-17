# 直し方の試作。(1) 枠の除去: 2 ページ以上で同じ位置に同じ文字が出るブロックと、左右の余白・上下の帯にあるブロックを外す。
# (2) 番号見出し: 行頭の番号の形で節を立てる。(3) 表: 罫線の表を検出し、枠の欄（承認・審査・日付だけの列）を外す。
import fitz, re, collections, json, unicodedata
HEAD=re.compile(r"^\s*(?P<h>(\d{1,2}[.．](?!\d)|\d{1,2}[-－]\d{1,2}|[（(]\d{1,2}[)）]|\d{1,2}[)）]|[a-z][.．]|別表\s*\d+|注\s*\d*|附則))\s*(?P<t>\S.*)?$")
FRAME_WORD=re.compile(r"^(配布先|承\s?認|審\s?査|作\s?成|控|原|合\s?計|番|号|改正回数|改正番号\s*\d*|来\s?歴|年\s?月\s?日|HS|[A-E]|'?\d{2}[./]\d{2}[./]\d{2}|\d{4}/\d{1,2}/\d{1,2}|回[汎管品生製技].{0,4}\s*\d?|技生技\s*\d?|Ishikawajima.*|IHK.*|石川島.*)$")
def norm(s): return re.sub(r"\s+","",unicodedata.normalize("NFKC",s or ""))
def frame_filter(doc):
    pages=[p.get_text("blocks") for p in doc]; W,H=doc[0].rect.width,doc[0].rect.height
    pos=collections.Counter()
    for bl in pages:
        for b in bl: pos[(round(b[0]/10),round(b[1]/10),norm(b[4])[:20])]+=1
    kept=[]; dropped=collections.Counter()
    for i,bl in enumerate(pages):
        rows=[]
        for b in bl:
            x0,y0,x1,y1,t=b[0],b[1],b[2],b[3],b[4]
            key=(round(x0/10),round(y0/10),norm(t)[:20])
            why=None
            if len(pages)>=2 and pos[key]>=2 and len(norm(t))<=24: why="repeat"
            elif x1<0.13*W: why="left"
            elif y1<0.08*H: why="top"
            elif y0>0.76*H: why="bottom"
            elif all(FRAME_WORD.match(l.strip()) for l in t.strip().splitlines() if l.strip()): why="frameword"
            if why: dropped[why]+=1; continue
            rows.append((y0,x0,t))
        rows.sort(); kept.append([t for _,_,t in rows])
    return kept, dropped
def sections(text):
    secs=[]; cur=("(前文)",[])
    for line in text.splitlines():
        l=line.strip()
        if not l: continue
        m=HEAD.match(l)
        if m and (m.group("t") or "") and len(m.group("t"))<=40 and not l.endswith("。"):
            secs.append(cur); cur=(l,[])
        else: cur[1].append(l)
    secs.append(cur); return [(h,b) for h,b in secs if b or h!="(前文)"]
out={}
with fitz.open("/docs/試運転要領/HS-VTR-T001E.pdf") as doc:
    kept,dropped=frame_filter(doc)
    text="\n".join("\n".join(t.rstrip() for t in page) for page in kept)
    secs=sections(text)
    out["T001"]={"dropped":dict(dropped),"sections":[h for h,_ in secs],"first":"\n".join(secs[0][1])[:600] if secs else "", "p1_after":"\n".join(kept[0])[:700]}
    raw_heads=[l.strip() for p in doc for l in p.get_text().splitlines() if HEAD.match(l.strip()) and not l.strip().endswith("。")]
    out["T001"]["raw_heads"]=len(raw_heads)
with fitz.open("/docs/RHAT/HS-RU-002F.pdf") as doc:
    page=doc[0]; tabs=page.find_tables()
    t=tabs.tables[0].extract() if tabs.tables else []
    def is_frame_col(j):
        col=[norm(r[j] or "") for r in t]; nonempty=[c for c in col if c]
        return not nonempty or all(FRAME_WORD.match(c) or re.fullmatch(r"'?\d{2}[./]\d{2}[./]\d{2}",c) for c in nonempty)
    drop=[j for j in range(len(t[0]))] if not t else [j for j in range(len(t[0])) if is_frame_col(j)]
    cleaned=[[c for j,c in enumerate(r) if j not in drop] for r in t]
    out["RU002"]={"table_before":t[:4],"frame_cols":drop,"table_after":cleaned[:5]}
print(json.dumps(out,ensure_ascii=False,indent=1)[:5000])
