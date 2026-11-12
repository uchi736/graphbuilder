#!/usr/bin/env python3
"""手直し 3（parser3）の計測を前回と並べる（ホストの python3 で動く。PDF は読まない）。

  HS 56 本: 前回 measure_full56_text/regions_text と、今回 measure_full56_parser3/regions_text
  本番 6 文書: 前回 measure_prod6_fix2/regions_text と、今回 measure_prod6_parser3/regions_text
どちらも convert.py（regions、図は text）の metrics_regions.json と <名前>.stats.json から、
  文字の残り（body・grid・none・all）、付属のラベルの見出し化、見出しの数、表の数と、表を文字層・格子に戻した数
を文書ごと・合計で並べる。今回の方が字を落としたページは、前回の出力にあって今回の出力に無い字（空白を除く字の多重集合の差）を書く。
出力: <今回の測定のディレクトリ>/compare_parser3.json・compare_parser3.md
"""
import json
import os
import re
import unicodedata
from collections import Counter

PARSER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETS = [("HS 56 本", "measure_full56_text/regions_text", "measure_full56_parser3/regions_text", "measure_full56_parser3"),
        ("本番 6 文書", "measure_prod6_fix2/regions_text", "measure_prod6_parser3/regions_text", "measure_prod6_parser3")]
TABLE_KEYS = ["tables", "tables_gfm", "tables_to_text", "tables_grid", "tables_pieces", "tables_history", "tables_missing_rows",
              "tables_misread", "tables_row_mismatch"]
PAGE_RX = re.compile(r"<!-- page:(\d+) -->\n(.*?)(?=\n\n<!-- page:|\Z)", re.S)


def load(folder):
    path = os.path.join(PARSER, folder, "metrics_regions.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    docs = {}
    for d in data["docs"]:
        stem = os.path.splitext(d["file"])[0]
        st = {}
        sp = os.path.join(PARSER, folder, stem + ".stats.json")
        if os.path.isfile(sp):
            with open(sp, encoding="utf-8") as f:
                st = json.load(f)
        ps = st.get("page_stats", [])
        d["_tables"] = {k: sum((p.get(k) or 0) for p in ps) for k in TABLE_KEYS}
        mp = os.path.join(PARSER, folder, stem + ".md")
        d["_pages"] = {}
        if os.path.isfile(mp):
            with open(mp, encoding="utf-8") as f:
                d["_pages"] = {int(m.group(1)): m.group(2) for m in PAGE_RX.finditer(f.read())}
        docs[d["file"]] = d
    return data, docs


def bag(text):
    t = unicodedata.normalize("NFKC", text or "")
    t = re.sub(r"[\s|]", "", t)
    return Counter(t)


def kept(d, kind="all"):
    r = (d.get("retention") or {}).get(kind) or {}
    return r.get("kept", 0), r.get("ref_chars", 0)


def ratio(k, n):
    return round(k / n, 4) if n else None


def compare(label, prev_dir, new_dir):
    _pd, prev = load(prev_dir)
    _nd, new = load(new_dir)
    rows, tot = [], {"prev": Counter(), "new": Counter()}
    lost_pages = []
    for name in sorted(set(prev) | set(new)):
        p, n = prev.get(name, {}), new.get(name, {})
        row = {"file": name}
        for key, d in (("prev", p), ("new", n)):
            ret = {k: (d.get("retention") or {}).get(k, {}).get("ratio") for k in ("body", "grid", "none", "all")}
            row[key] = {"retention": ret, "annex": (d.get("annex") or {}).get("headings"), "annex_expected": (d.get("annex") or {}).get("expected"),
                        "headings": (d.get("headings") or {}).get("count"), "tables": d.get("_tables", {}), "failed": bool(d.get("failed"))}
            for kind in ("body", "grid", "none", "all"):
                k, r = kept(d, kind)
                tot[key][f"kept_{kind}"] += k
                tot[key][f"ref_{kind}"] += r
            tot[key]["annex"] += (d.get("annex") or {}).get("headings") or 0
            tot[key]["annex_expected"] += (d.get("annex") or {}).get("expected") or 0
            tot[key]["headings"] += (d.get("headings") or {}).get("count") or 0
            tot[key]["admin_leaked"] += (d.get("admin_cells") or {}).get("leaked") or 0
            tot[key]["admin_total"] += (d.get("admin_cells") or {}).get("total") or 0
            for k, v in d.get("_tables", {}).items():
                tot[key][k] += v
        kp, _ = kept(p)
        kn, _ = kept(n)
        row["kept_diff"] = kn - kp
        # ページごとに、今回の方が残った字の少ないページ
        pp = {x["page"]: x for x in p.get("per_page", [])}
        for x in n.get("per_page", []):
            y = pp.get(x["page"])
            if not y or not x.get("ref_chars"):
                continue
            a = round((y.get("retention") or 0) * y["ref_chars"])
            b = round((x.get("retention") or 0) * x["ref_chars"])
            if b < a:
                gone = bag(p["_pages"].get(x["page"], "")) - bag(n["_pages"].get(x["page"], ""))
                lost_pages.append({"file": name, "page": x["page"], "kind": x.get("kind"), "kept_prev": a, "kept_new": b,
                                   "ref": x["ref_chars"], "chars_only_in_prev": "".join(sorted(gone.elements()))[:200]})
        rows.append(row)
    summary = {}
    for key in ("prev", "new"):
        t = tot[key]
        summary[key] = {"retention": {k: ratio(t[f"kept_{k}"], t[f"ref_{k}"]) for k in ("body", "grid", "none", "all")},
                        "kept_all": t["kept_all"], "ref_all": t["ref_all"],
                        "annex": [t["annex"], t["annex_expected"]], "headings": t["headings"],
                        "admin_leaked": [t["admin_leaked"], t["admin_total"]], "tables": {k: t[k] for k in TABLE_KEYS}}
    return {"label": label, "prev": prev_dir, "new": new_dir, "summary": summary, "docs": rows, "lost_pages": lost_pages}


def markdown(res):
    s = res["summary"]
    out = [f"## {res['label']}（前回 {res['prev']} / 今回 {res['new']}）", "",
           "| 指標 | 前回 | 今回 |", "|---|---|---|"]
    for k in ("body", "grid", "none", "all"):
        out.append(f"| 文字の残り {k} | {s['prev']['retention'][k]} | {s['new']['retention'][k]} |")
    out.append(f"| 残った字 / 参照の字（all） | {s['prev']['kept_all']} / {s['prev']['ref_all']} | {s['new']['kept_all']} / {s['new']['ref_all']} |")
    out.append(f"| 付属のラベルの見出し化 | {s['prev']['annex'][0]}/{s['prev']['annex'][1]} | {s['new']['annex'][0]}/{s['new']['annex'][1]} |")
    out.append(f"| 見出しの数 | {s['prev']['headings']} | {s['new']['headings']} |")
    out.append(f"| 枠の外の欄の混入 | {s['prev']['admin_leaked'][0]}/{s['prev']['admin_leaked'][1]} | {s['new']['admin_leaked'][0]}/{s['new']['admin_leaked'][1]} |")
    names = {"tables": "表の数", "tables_gfm": "Nemotron の表のまま GFM", "tables_to_text": "文字層に戻した表（合計）",
             "tables_grid": "　うち罫線から格子の GFM", "tables_pieces": "　うちかけらを縦棒で区切った行", "tables_history": "　うち来歴の表（本文の行のまま）",
             "tables_missing_rows": "　戻した理由: 欠け行", "tables_misread": "　戻した理由: 読み誤り", "tables_row_mismatch": "　戻した理由: 行ごとの整合"}
    for k in TABLE_KEYS:
        out.append(f"| {names[k]} | {s['prev']['tables'].get(k, 0)} | {s['new']['tables'].get(k, 0)} |")
    out += ["", "文書ごとの差（残った字・見出し・付属・表を戻した数のどれかが変わった文書）:", "",
            "| 文書 | 残った字の差 | 文字の残り all 前回→今回 | 見出し 前回→今回 | 付属 前回→今回 | 戻した表 前回→今回（格子） |", "|---|---|---|---|---|---|"]
    for r in res["docs"]:
        p, n = r["prev"], r["new"]
        changed = (r["kept_diff"] or p["headings"] != n["headings"] or p["annex"] != n["annex"]
                   or p["tables"].get("tables_to_text") != n["tables"].get("tables_to_text"))
        if changed:
            out.append(f"| {r['file']} | {r['kept_diff']:+d} | {p['retention']['all']}→{n['retention']['all']} | {p['headings']}→{n['headings']} | "
                       f"{p['annex']}/{p['annex_expected']}→{n['annex']}/{n['annex_expected']} | "
                       f"{p['tables'].get('tables_to_text', 0)}→{n['tables'].get('tables_to_text', 0)}（{n['tables'].get('tables_grid', 0)}） |")
    if res["lost_pages"]:
        out += ["", "今回の方が字を落としたページ（前回の出力にあって今回の出力に無い字。表の「|」と空白は数えない）:", "",
                "| 文書 | ページ | 区分 | 残った字 前回→今回 / 参照 | 前回にだけある字 |", "|---|---|---|---|---|"]
        for x in res["lost_pages"]:
            out.append(f"| {x['file']} | {x['page']} | {x['kind']} | {x['kept_prev']}→{x['kept_new']} / {x['ref']} | {x['chars_only_in_prev']} |")
    return "\n".join(out) + "\n"


def main():
    for label, prev_dir, new_dir, out_dir in SETS:
        if not os.path.isfile(os.path.join(PARSER, new_dir, "metrics_regions.json")):
            print(f"{label}: 今回の計測が無い（{new_dir}）")
            continue
        res = compare(label, prev_dir, new_dir)
        with open(os.path.join(PARSER, out_dir, "compare_parser3.json"), "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
        md = markdown(res)
        with open(os.path.join(PARSER, out_dir, "compare_parser3.md"), "w", encoding="utf-8") as f:
            f.write(md)
        print(md)


if __name__ == "__main__":
    main()
