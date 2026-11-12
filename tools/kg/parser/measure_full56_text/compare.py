#!/usr/bin/env python3
"""HS 56 本の regions（図は text）と今の変換（pymupdf4llm）の指標を文書ごとに並べる（convert.py の metrics_*.json と .stats.json から）。
ホストの python3 で動く（PDF は読まない）。出力: compare.json・compare.tsv"""
import json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
reg = json.load(open(os.path.join(HERE, "regions_text", "metrics_regions.json"), encoding="utf-8"))
conv = json.load(open(os.path.join(HERE, "regions_text", "metrics_regions.conversion_run.json"), encoding="utf-8"))
pym = json.load(open(os.path.join(HERE, "pymupdf4llm", "metrics_pymupdf4llm.json"), encoding="utf-8"))
P = {d["file"]: d for d in pym["docs"]}
C = {d["file"]: d for d in conv["docs"]}
rows = []
for d in reg["docs"]:
    f = d["file"]; p = P.get(f, {}); stem = os.path.splitext(f)[0]
    st = {}
    sp = os.path.join(HERE, "regions_text", stem + ".stats.json")
    if os.path.isfile(sp):
        st = json.load(open(sp, encoding="utf-8"))
    ps = st.get("page_stats", [])
    text_only = [x["page"] for x in ps if not x.get("needs_parse", True) or x.get("fallback")]
    row = {"file": f, "pages": d.get("pages"), "seconds": C.get(f, {}).get("seconds"), "failed": bool(d.get("failed")),
           "parse_failed_pages": st.get("parse_failed_pages"), "no_text_layer_pages": st.get("no_text_layer_pages"),
           "text_only_pages": len(text_only), "text_only_list": text_only,
           "kinds": {k: sum(1 for x in ps if x.get("kind") == k) for k in ("body", "grid", "none")},
           "tables": sum(x.get("tables", 0) for x in ps), "tables_to_text": sum(x.get("tables_to_text", 0) for x in ps),
           "tables_missing_rows": sum(x.get("tables_missing_rows", 0) for x in ps),
           "ret_regions": {k: v["ratio"] for k, v in d["retention"].items()},
           "ret_pymu": {k: v["ratio"] for k, v in (p.get("retention") or {}).items()},
           "annex_regions": [d["annex"]["headings"], d["annex"]["expected"]],
           "annex_pymu": [(p.get("annex") or {}).get("headings"), (p.get("annex") or {}).get("expected")],
           "annex_detail_regions": d["annex"]["detail"], "annex_detail_pymu": (p.get("annex") or {}).get("detail"),
           "admin_regions": [d["admin_cells"]["leaked"], d["admin_cells"]["total"]],
           "admin_pymu": [(p.get("admin_cells") or {}).get("leaked"), (p.get("admin_cells") or {}).get("total")],
           "admin_examples_regions": d["admin_cells"]["examples"],
           "headings_regions": d["headings"]["count"], "headings_pymu": (p.get("headings") or {}).get("count"),
           "levels_regions": d["headings"]["by_level"], "levels_pymu": (p.get("headings") or {}).get("by_level"),
           "chunks_regions": (d.get("sections") or {}).get("chunks"), "chunks_pymu": (p.get("sections") or {}).get("chunks"),
           "sections_regions": (d.get("sections") or {}).get("sections"), "sections_pymu": (p.get("sections") or {}).get("sections"),
           "annexsec_regions": (d.get("sections") or {}).get("annex_labels"), "annexsec_pymu": (p.get("sections") or {}).get("annex_labels"),
           "chapters_regions": (d.get("sections") or {}).get("chapters"), "chapters_pymu": (p.get("sections") or {}).get("chapters"),
           "seconds_pymu": p.get("seconds"),
           "per_page_regions": d.get("per_page"), "per_page_pymu": p.get("per_page")}
    rows.append(row)
json.dump(rows, open(os.path.join(HERE, "compare.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
cols = ["file", "pages", "seconds", "failed", "text_only_pages", "tables", "tables_to_text", "tables_missing_rows"]
with open(os.path.join(HERE, "compare.tsv"), "w", encoding="utf-8") as out:
    out.write("\t".join(cols + ["ret_all_regions", "ret_all_pymu", "ret_body_regions", "ret_body_pymu", "annex_regions", "annex_pymu",
                                "admin_regions", "headings_regions", "headings_pymu", "chunks_regions", "chunks_pymu"]) + "\n")
    for r in rows:
        out.write("\t".join(str(r[c]) for c in cols) + "\t" + "\t".join(str(x) for x in [
            r["ret_regions"].get("all"), r["ret_pymu"].get("all"), r["ret_regions"].get("body"), r["ret_pymu"].get("body"),
            "%s/%s" % tuple(r["annex_regions"]), "%s/%s" % tuple(r["annex_pymu"]), "%s/%s" % tuple(r["admin_regions"]),
            r["headings_regions"], r["headings_pymu"], r["chunks_regions"], r["chunks_pymu"]]) + "\n")
tot = lambda key: sum((r.get(key) or 0) for r in rows)
summary = {"docs": len(rows), "pages": tot("pages"), "seconds_regions_sum": round(tot("seconds"), 1),
           "seconds_pymu_sum": round(tot("seconds_pymu"), 1), "failed": [r["file"] for r in rows if r["failed"]],
           "text_only_pages": tot("text_only_pages"), "tables": tot("tables"), "tables_to_text": tot("tables_to_text"),
           "tables_missing_rows": tot("tables_missing_rows"),
           "retention_regions": reg["summary"]["retention"], "retention_pymu": pym["summary"]["retention"],
           "annex_regions": reg["summary"]["annex"], "annex_pymu": pym["summary"]["annex"],
           "admin_regions": reg["summary"]["admin_cells_leaked"], "admin_pymu": pym["summary"]["admin_cells_leaked"],
           "headings_regions": tot("headings_regions"), "headings_pymu": tot("headings_pymu"),
           "chunks_regions": tot("chunks_regions"), "chunks_pymu": tot("chunks_pymu"),
           "calls": reg["summary"]["calls"]}
json.dump(summary, open(os.path.join(HERE, "compare_summary.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(json.dumps(summary, ensure_ascii=False))
