# 枠を「表」として読む試作。ページ全体を覆う罫線の表を枠とみなし、いちばん大きなセルを本文、残りのセルを枠の欄として分ける。
# 本文のセルの中だけで、改めて表を探す。
import fitz, re, unicodedata, json
def norm(s): return re.sub(r"\s+"," ",unicodedata.normalize("NFKC",s or "")).strip()
def analyze(path, pno=0):
    doc=fitz.open(path); page=doc[pno]; A=page.rect.get_area()
    tabs=page.find_tables()
    out={"file":path.split("/")[-1],"page":pno+1,"tables_on_page":len(tabs.tables)}
    frame=None
    for t in tabs.tables:
        r=fitz.Rect(t.bbox)
        if r.get_area()/A>0.7: frame=t; break
    if not frame:
        out["frame"]="ページを覆う表は無い"; print(json.dumps(out,ensure_ascii=False)); return
    cells=[fitz.Rect(c) for c in frame.cells if c]
    body=max(cells,key=lambda r:r.get_area())
    out["frame_bbox"]=[round(v) for v in frame.bbox]; out["frame_cells"]=len(cells); out["body_cell"]=[round(v) for v in body]
    out["body_share"]=round(body.get_area()/fitz.Rect(frame.bbox).get_area(),2)
    body_text=page.get_text("text",clip=body)
    out["body_head"]=norm(body_text)[:260]
    others=[]
    for c in cells:
        if c==body: continue
        t=norm(page.get_text("text",clip=c))
        if t: others.append(t[:30])
    out["frame_cells_text"]=others[:16]
    inner=page.find_tables(clip=body)
    out["inner_tables"]=len(inner.tables)
    if inner.tables:
        t=inner.tables[0].extract(); out["inner_table_rows"]=[[norm(c) if c else "" for c in r] for r in t[:4]]
        out["inner_table_bbox"]=[round(v) for v in inner.tables[0].bbox]
    # 本文のセルから表の領域を除いた文字（表以外の本文）
    frame_words=re.compile(r"配布先|承\s?認|審\s?査|来\s?歴|改正回数")
    out["frame_words_in_body"]=len(frame_words.findall(body_text))
    print(json.dumps(out,ensure_ascii=False,indent=1))
for p,n in (("/docs/試運転要領/HS-VTR-T001E.pdf",0),("/docs/試運転要領/HS-VTR-T001E.pdf",3),("/docs/RHAT/HS-RU-002F.pdf",0),("/docs/TPS/HS-TP-004J 本文.pdf",0),("/docs/RHAT/HS-RU-016A高周波焼入れ本文.pdf",0)): analyze(p,n)
