# 59 本の全ページで「ページを覆う罫線の表」がどう見つかるかを数える。汎化性の材料。
import fitz, glob, os, re, json, collections, inspect


rows=[]
for d in ("RHAT","TPS","試運転要領"):
    for p in sorted(glob.glob(f"/docs/{d}/*.pdf")):
        name=os.path.basename(p)
        with fitz.open(p) as doc:
            for i,page in enumerate(doc):
                A=page.rect.get_area(); txt=page.get_text()
                if len(txt.strip())<20: rows.append({"d":d,"f":name,"p":i+1,"kind":"文字層なし"}); continue
                tabs=page.find_tables().tables
                frame=[t for t in tabs if fitz.Rect(t.bbox).get_area()/A>=0.7]
                if not frame: rows.append({"d":d,"f":name,"p":i+1,"kind":"枠なし","tables":len(tabs)}); continue
                t=frame[0]; cells=[fitz.Rect(c) for c in t.cells if c]; FA=fitz.Rect(t.bbox).get_area()
                shares=sorted([c.get_area()/FA for c in cells],reverse=True)
                body=max(cells,key=lambda r:r.get_area()); bt=page.get_text("text",clip=body)
                kind="本文1セル" if shares[0]>=0.4 else ("格子" if len(cells)>=12 and shares[0]<0.25 else "大セル複数")
                rows.append({"d":d,"f":name,"p":i+1,"kind":kind,"cells":len(cells),"s1":round(shares[0],2),"s2":round(shares[1],2) if len(shares)>1 else 0,"body_chars":len(re.sub(r"\s","",bt)),"page_chars":len(re.sub(r"\s","",txt)),"inner":len(page.find_tables(clip=body).tables)})
c=collections.Counter(r["kind"] for r in rows); print("ページの区分:", dict(c), "| 全ページ", len(rows))
ok=[r for r in rows if r["kind"]=="本文1セル"]
import statistics
print("本文1セル: 本文セルの面積比 中央値 %.2f 最小 %.2f | 2 番目のセル 中央値 %.2f 最大 %.2f | 本文の文字の割合 中央値 %.0f%%" % (statistics.median(r["s1"] for r in ok), min(r["s1"] for r in ok), statistics.median(r["s2"] for r in ok), max(r["s2"] for r in ok), 100*statistics.median(r["body_chars"]/max(r["page_chars"],1) for r in ok)))
low=[(r["f"][:22],r["p"],round(r["body_chars"]/max(r["page_chars"],1),2)) for r in ok if r["body_chars"]/max(r["page_chars"],1)<0.6]; print("本文セルの文字が 6 割未満のページ:", low[:12])
for k in ("枠なし","格子","大セル複数"):
    xs=[(r["f"][:24],r["p"]) for r in rows if r["kind"]==k]; print(k, len(xs), xs[:14])
bydoc=collections.defaultdict(collections.Counter)
for r in rows: bydoc[r["f"]][r["kind"]]+=1
print("文書ごとに本文1セルでないページがある文書:", [(f[:22],dict(c)) for f,c in bydoc.items() if set(c)-{"本文1セル"}][:20])
json.dump(rows,open("/work/frames.json","w",encoding="utf-8"),ensure_ascii=False,indent=1)
