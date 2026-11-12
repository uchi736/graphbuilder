import json, re, collections, sys, datetime
OUT = sys.argv[1]; DEST = sys.argv[2]
inv = json.load(open(f"{OUT}/inventory_raw.json"))
cov = json.load(open(f"{OUT}/coverage_5gram.json"))
cmp_ = json.load(open(f"{OUT}/chunk_compare.json"))
labels = json.load(open(f"{OUT}/labels_all.json"))
tables = json.load(open(f"{OUT}/tables_pymupdf.json"))
texts = {}
for line in open(f"{OUT}/pagetext.jsonl", encoding="utf-8"):
    r = json.loads(line); texts[(r["file"], r["page"])] = r["text"]
STAMP_PX = {(520, 177), (1040, 354), (1041, 354)}
FRAME = re.compile(r"来歴|配布先|改正番号|作成審査|審査承認|承認|株式会社|改正回数|年月日|控|合計|回品|IshikawajimaMass|新規制定|新規作成|新規調整|^[A-Z]{4,}$|[A-W]{6,}|酒井|大槻|宇治|回技術部|品管G|回品管|過検査課|^HS\d|協議|同意|審査結果|作成部門|番号HS")
KW = {k: re.compile(r"(?<!識)" + r"[\s　]*".join(k)) for k in ["別表", "付表", "添付資料"]}
def tclass(t):
    x0, y0, x1, y1 = t["bbox"]; a = (x1 - x0) * (y1 - y0)
    if a >= 0.6: return "page_frame"
    if x1 <= 0.2: return "distribution_column"
    if y0 >= 0.78: return "history_block"
    if y1 <= 0.12: return "title_block"
    return "body_table"
tab_by = collections.defaultdict(list)
for t in tables: tab_by[(t["file"], t["page"])].append(tclass(t))
def is_form(fn, pg):
    s = re.sub(r"\s", "", texts.get((fn, pg), ""))
    return ("協議" in s and "通知票" in s) or "発行目録" in fn or "廃止通知" in fn

# 見出しの位置にあるラベル(上端 12% 以内・字高 12pt 以上、または上端 12% 以内でラベルだけの行)
heading_labels = []
for e in labels:
    if e["kind"] in ("別表", "付表", "添付資料", "別紙") and e["y_top"] <= 0.12 and not (e["kind"] == "別紙" and e["num"] == "3"):
        heading_labels.append(e)
CHUNK_HEADING = {  # graph_dump_linked.json の chunks で、そのページから始まるチャンクの section(目視で確認済み)
    ("VTR-T005B.pdf", 3): ("コンプレッサ性能計測要領", "ラベル「別表1」はどのチャンクにも入らず消えている"),
    ("VTR-T005B.pdf", 4): ("過給機性能試験及び性能換算法", "ラベル「別表2」はどのチャンクにも入らず消えている。p2 の「ABB方式 別表2による」から p4 への refers_to は無い"),
    ("VTR-T005B.pdf", 5): ("ABB方式 コンプレッサ性能", "ラベル「添付資料」は前ページのチャンク(position 12、p4)の末尾に付いている"),
    ("VTR-T004C.pdf", 3): ("コンプレッサ性能計測要領", "ラベル「別表1」は見出しに付かない"),
    ("VTR-T004C.pdf", 4): ("ABB方式 コンプレッサ性能", "ラベル「添付資料」は前ページのチャンク(position 7、p3)の末尾に付いている"),
    ("HS-VTR-T002F.pdf", 4): ("コンプレッサ性能計測要領", "ラベル「別 表 １」(字間に空白)は見出しに付かない"),
    ("HS-VTR-T002F.pdf", 5): ("別表2 過給機性能試験及び性能換算法", "正しく見出しになっている唯一の例"),
    ("HS-VTR-T002F.pdf", 7): ("別表2 過給機性能試験及び性能換算法 > 3. サージ誤差及び回転誤差計算方法", "「HS VTR-T002F 添付資料」は p6 のチャンクの途中に入り、添付資料の内容が別表2 の下に入っている"),
    ("HS-TP-023.pdf", 5): ("3. 基準制定来歴(p4 から続くチャンク)", "「HS-TP-023 付表」は見出しにならず、付表の受検一覧表が「3. 基準制定来歴」の節に入っている"),
    ("VTR-T003D.pdf", 2): ("4. 基準来歴 > コンプレッサ性能管理曲線管理台帳", "「HS-VTR-T003C 別紙-1」は見出しに付かず、別紙が「4. 基準来歴」の子の節になっている"),
}
docs = []; tot = collections.Counter()
for e in inv["docs"]:
    if "same_as" in e:
        docs.append({"group": e["group"], "file": e["file"], "md5": e["md5"], "same_as": e["same_as"], "note": "full56 の同名ファイルとバイト単位で同一。数値は same_as を参照"})
        continue
    fn = e["file"]; c = cmp_["full56"].get(fn, {}); cv = cov.get(fn, {})
    pages = []
    for p in e["page_info"]:
        pg = p["page"]; k = str(pg)
        imgs = p["images"]
        strips = [i for i in imgs if i["px"] and i["px"][0] >= 4000 and (i["area"] or 0) >= 0.05]
        stamps = [i for i in imgs if (i["px"] and tuple(i["px"]) in STAMP_PX) or ((i["area"] or 0) < 0.01)]
        figs = [i for i in imgs if (i["area"] or 0) >= 0.01 and not (i["px"] and tuple(i["px"]) in STAMP_PX) and i not in strips]
        tcls = collections.Counter(tab_by.get((fn, pg), []))
        v = cv.get(k, {})
        content_spans = [s for s in v.get("uncovered_spans", []) if not FRAME.search(s)]
        t = texts.get((fn, pg), "")
        kw = {name: len(rx.findall(t)) for name, rx in KW.items() if rx.search(t)}
        pages.append({
            "page": pg, "paper": p["paper"], "orientation": p["orientation"], "rotation": p["rotation"], "w_mm": p["w_mm"], "h_mm": p["h_mm"],
            "text_layer_chars": p["chars"], "no_text_layer": p["chars"] < 50, "garbled_chars": p["weird_chars"],
            "image_objs": p["image_objs"], "image_objs_small_or_stamp": len(stamps), "figure_images": len(figs),
            "strip_scan": bool(strips), "image_area_sum": p["image_area_sum"],
            "path_objs": p["path_objs"],
            "tables_pymupdf": dict(tcls), "framed_page": tcls.get("page_frame", 0) > 0, "form_page": is_form(fn, pg),
            "keywords": kw,
            "chunk_coverage": {"uncovered_chars_5gram": v.get("uncovered_chars"), "uncovered_ratio": v.get("uncovered_ratio"),
                               "uncovered_content_chars": sum(len(s) for s in content_spans), "uncovered_content_spans": content_spans[:20]},
            "chunks_starting_here": c.get("pages", {}).get(k, {}).get("chunks_starting"),
            "head_lines": p["head_lines"][:4],
        })
    layer = sum(x["text_layer_chars"] for x in pages)
    d = {"group": e["group"], "file": fn, "md5": e["md5"], "bytes": e["bytes"], "pdf_version": e["pdf_version"],
         "pages": e["pages"], "papers": sorted({f"{x['paper']}-{x['orientation']}" for x in pages}), "rotations": sorted({x["rotation"] for x in pages}),
         "no_text_layer_pages": [x["page"] for x in pages if x["no_text_layer"]],
         "strip_scan_pages": [x["page"] for x in pages if x["strip_scan"]],
         "image_objs": sum(x["image_objs"] for x in pages), "figure_images": sum(x["figure_images"] for x in pages),
         "body_tables_pymupdf": sum(x["tables_pymupdf"].get("body_table", 0) for x in pages),
         "framed_pages": sum(1 for x in pages if x["framed_page"]), "form_pages": [x["page"] for x in pages if x["form_page"]],
         "text_layer_chars": layer,
         "chunks": c.get("chunks"), "chunk_chars_raw": c.get("chunk_chars_raw"), "chunk_chars_norm": c.get("chunk_chars_body_norm"),
         "chunk_to_layer_ratio": round(c["chunk_chars_body_norm"] / max(1, sum(int(v["layer_chars"]) for v in c["pages"].values())), 3) if c else None,
         "uncovered_chars_5gram": sum(x["chunk_coverage"]["uncovered_chars_5gram"] or 0 for x in pages),
         "uncovered_content_chars": sum(x["chunk_coverage"]["uncovered_content_chars"] for x in pages),
         "keyword_pages": {kname: [x["page"] for x in pages if kname in x["keywords"]] for kname in KW},
         "page_info": pages}
    docs.append(d)
    for kk in ("pages", "image_objs", "figure_images", "body_tables_pymupdf", "framed_pages", "text_layer_chars", "uncovered_chars_5gram", "uncovered_content_chars"):
        tot[kk] += d[kk] or 0
    tot["no_text_layer_pages"] += len(d["no_text_layer_pages"]); tot["strip_scan_pages"] += len(d["strip_scan_pages"])
    tot["form_pages"] += len(d["form_pages"]); tot["chunk_chars_norm"] += d["chunk_chars_norm"] or 0
    tot["rotated_pages"] += sum(1 for x in pages if x["rotation"]); tot["landscape_pages"] += sum(1 for x in pages if x["orientation"] == "landscape")

uniq_pages = tot["pages"]; dup_pages = sum(next(d for d in docs if d.get("file") == x["file"] and "same_as" not in d)["pages"] for x in docs if "same_as" in x)
fig_raster = tot["figure_images"]; fig_strip = (8 * 2); fig_vector = (20, 40); tables_body = tot["body_tables_pymupdf"]; framed = tot["framed_pages"]
def mins(s): return round(s / 60, 1)
figs = (fig_raster + fig_strip + fig_vector[0], fig_raster + fig_strip + fig_vector[1])
est = {
 "assumptions": {"nemotron_parse_sec_per_page_serial": 3, "nemotron_parse_pages_per_sec_2parallel": 1.4, "e4b_sec_per_region": 2,
                 "gemma26b_sec_per_region": [10, 20], "gemma26b_parallel": 3,
                 "note_26b_parallel": "gemma4 は画像付きの同時 6 本でハングした記録があり、画像付きは同時 3 本までとして計算",
                 "e4b_parallel": 2},
 "pages": {"unique_56_docs": uniq_pages, "with_prod6_duplicates_62_docs": uniq_pages + dup_pages},
 "regions": {
   "figure_raster": fig_raster, "figure_raster_note": "面積 1% 以上の画像(承認印・電子文書印・「改正」朱印は除く)。目視した範囲では断面図などの線画で、写真は見当たらない",
   "figure_in_strip_scans": fig_strip, "figure_in_strip_scans_note": "帯状スキャン 8 ページ×約 2(目視で 1〜3)",
   "figure_vector_estimate": list(fig_vector), "figure_vector_note": "線(パス)で描かれた図は画像の数に出ない(例 VTR-T005B p3 のグラフ 3 つ)。パスが多いページ 18 と別表1 のグラフのページからの推定",
   "figures_total": list(figs),
   "tables_body_pymupdf": tables_body, "tables_note": "PyMuPDF find_tables で、ページ枠・配布先の列・来歴欄を除いたもの。上限寄りの数",
   "framed_pages": framed, "framed_note": "ページ全体を囲む枠のあるページ。Nemotron Parse は枠付きの規格をページ全体で 1 つの表として返すことがある(10/29 の表ベンチ)。その場合 1 ページに 1 つ大きな Table 領域が増える"},
}
np_serial = uniq_pages * 3; np_par = uniq_pages / 1.4
e4b_base = (figs[0] + tables_body) * 2, (figs[1] + tables_body) * 2
e4b_frame = framed * 2
caseA = (figs[0] * 10, figs[1] * 20)                         # 図だけ 26B、表は文字層(または Nemotron の表)から
caseB = ((figs[0] + tables_body) * 10, (figs[1] + tables_body) * 20)  # 図と表を 26B
caseC_extra = (framed * 10, framed * 20)                      # 枠の大きな Table を E4B が見逃して 26B に回した場合
est["seconds"] = {"nemotron_parse": {"serial": np_serial, "parallel2": round(np_par)},
                  "e4b": {"serial": list(e4b_base), "serial_with_frame_tables": [e4b_base[0] + e4b_frame, e4b_base[1] + e4b_frame]},
                  "gemma26b": {"A_figures_only": list(caseA), "B_figures_and_tables": list(caseB), "C_extra_if_frame_tables_go_to_26b": list(caseC_extra)}}
def total(case, par):
    lo = (np_par if par else np_serial) + (e4b_base[0] / (2 if par else 1)) + case[0] / (3 if par else 1)
    hi = (np_par if par else np_serial) + (e4b_base[1] + e4b_frame) / (2 if par else 1) + case[1] / (3 if par else 1)
    return [mins(lo), mins(hi)]
est["total_minutes"] = {
  "A_figures_only_serial": total(caseA, False), "A_figures_only_parallel": total(caseA, True),
  "B_figures_and_tables_serial": total(caseB, False), "B_figures_and_tables_parallel": total(caseB, True),
  "C_worst_serial": total((caseB[0] + caseC_extra[0], caseB[1] + caseC_extra[1]), False),
  "C_worst_parallel": total((caseB[0] + caseC_extra[0], caseB[1] + caseC_extra[1]), True),
  "note": "parallel = Nemotron 2 本・E4B 2 本・26B 3 本。下限は枠の Table なし、上限は枠の Table を E4B に回す分を含む。62 本(本番 6 本を重ねて流す)ならページ数で約 1.1 倍"}

result = {
 "generated": datetime.datetime.now().isoformat(timespec="seconds"),
 "method": "pypdfium2 5.14.0(イメージ llm-graph-builder-backend:latest、--memory=4g --network none)。表の数だけ PyMuPDF find_tables(pymupdf4llm は読み込まない)。チャンクは full56_night1109/graph_dump_linked.json と prod_night1109/graph_dump.json",
 "definitions": {
   "text_layer_chars": "ページの文字層の文字数(空白を除く)", "no_text_layer": "text_layer_chars < 50",
   "figure_images": "面積 1% 以上の画像オブジェクト(印影・朱印・帯状スキャンを除く)", "strip_scan": "ページ幅いっぱいの帯状の画像(幅 4000px 以上)が並ぶページ。スキャンを帯に分けて貼ったもの。本文は文字層にもある",
   "uncovered_chars_5gram": "文字層の文字のうち、同じファイルのチャンク本文(NFKC・空白除去)の 5 文字の並びで覆われない文字の数",
   "uncovered_content_chars": "上のうち、管理欄(来歴・配布先・承認・改正番号など。今の変換が意図して外している)と発行目録の改訂記号の列を除いたもの",
   "keywords": "「別表」「付表」「添付資料」の出現数(字間の空白を許し、「識別表示」は除く)"},
 "summary": dict(tot, docs_unique=sum(1 for d in docs if "same_as" not in d), docs_listed=len(docs), duplicate_docs=[d["file"] for d in docs if "same_as" in d],
                 duplicate_pages=dup_pages),
 "heading_labels": [dict(e, chunk_section=CHUNK_HEADING.get((e["file"], e["page"]), (None, None))[0], finding=CHUNK_HEADING.get((e["file"], e["page"]), (None, None))[1]) for e in heading_labels],
 "label_occurrences": labels,
 "estimate": est,
 "docs": docs,
}
json.dump(result, open(DEST, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(json.dumps(result["summary"], ensure_ascii=False))
print(json.dumps(est["regions"], ensure_ascii=False)[:600])
print(json.dumps(est["seconds"], ensure_ascii=False)); print(json.dumps(est["total_minutes"], ensure_ascii=False))
for h in result["heading_labels"]: print(h["file"], h["page"], h["kind"], h["num"], h["y_top"], h["chunk_section"])
