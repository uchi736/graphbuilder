#!/usr/bin/env python3
"""HS 56 本の補足の集計（一時コンテナ、--network none）。
  - 文字層の全ページの付属のラベルの語（別表・付表・別紙・添付資料）を含むかけら（位置つき）と、上端 15% のものが見出しになったか
  - regions が今の変換より字を落としたページの、失った字（regions・pymupdf4llm）
出力: detail.json"""
import glob, json, os, re, sys
sys.path.insert(0, "/tool"); sys.path.insert(0, "/tool/measure_prod6"); sys.path.insert(0, "/code")
import convert as CV
import measure as M
from pathlib import Path
HERE = "/tool/measure_full56_text"
PAGES = [("TPS48BH19abura-2", 1), ("HS-RU-004E", 3), ("TPS48BH19abura", 1), ("HS-TP-017D", 1), ("HS-TP-002C", 13), ("HS-TP-002C", 10),
         ("HS-RU-016A高周波焼入れ", 1), ("VTR-T005B", 3), ("HS-TP-023", 5), ("VTR-T004C", 3), ("HS-RU-006J", 4), ("HS-TP-002C", 15),
         ("HS-RU発行目録_H24.4.3", 1), ("HS-TP発行目録_H23.9.27", 1), ("VTR-T_発行目録", 1)]
out = {"annex_scan": {}, "lost": []}
for path in sorted(glob.glob("/pdf/*.pdf")):
    stem = Path(path).stem
    scan = M.annex_scan(path)
    if scan:
        md = open(os.path.join(HERE, "regions_text", stem + ".md"), encoding="utf-8").read()
        mdp = open(os.path.join(HERE, "pymupdf4llm", stem + ".md"), encoding="utf-8").read()
        heads = [t for _, t in M.headings_of(md)]
        headsp = [t for _, t in M.headings_of(mdp)]
        for s in scan:
            key = re.sub(r"\s+", "", s["text"])[:12]
            s["in_heading_regions"] = [h for h in heads if key[:6] and key[:6] in re.sub(r"\s+", "", h)][:2]
            s["in_heading_pymu"] = [h for h in headsp if key[:6] and key[:6] in re.sub(r"\s+", "", h)][:2]
        out["annex_scan"][stem] = scan
refs = {}
for stem, page in PAGES:
    path = f"/pdf/{stem}.pdf"
    if stem not in refs:
        refs[stem] = CV.reference(path)
    ref = refs[stem][page - 1]
    row = {"doc": stem, "page": page, "kind": ref["kind"]}
    for conv in ("regions_text", "pymupdf4llm"):
        md = open(os.path.join(HERE, conv, stem + ".md"), encoding="utf-8").read()
        pages = {int(m.group(1)): m.group(2) for m in CV.PAGE_RX.finditer(md)}
        row[conv] = M.lost_chars(ref["ref"], pages.get(page, ""))
    out["lost"].append(row)
json.dump(out, open(os.path.join(HERE, "detail.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("written")
