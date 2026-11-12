#!/usr/bin/env python3
"""本番 6 文書の手直し後（parser2）の計測。measure_prod6/measure.py と同じ指標を、前回の regions_text と今回の出力で並べる。

  - convert.py の指標（metrics_regions.json）: 本文の文字の残り（body・none・grid・all）、枠の外の混入、付属ラベルの見出し化、見出しの数と段
  - measure.py の指標: 別表2 の見出しと付属の節、節番号の並び（今の変換との一致）、チャンク数、目録の節（数・形の内訳・付属の節）、失った字
  - 直した箇所の確認: 3-1 サージ誤差計算の節のチャンクに「実測風量」「マップ風量」が入るか（S25 の答えの箇所）

使い方（一時コンテナ。外へは出ない）:
  cd ~/ai-stack/llm-graph-builder && docker run --rm --memory=4g --network none --user 1000:1000 -e HOME=/tmp -e USER=uchida \\
    -e PYTHONDONTWRITEBYTECODE=1 -v "$PWD/backend:/code:ro" -v "$PWD/tools/kg/parser:/tool" \\
    -v "$PWD/tools/kg/hs/runs/prod_night1109/pdf:/pdf:ro" -w /code llm-graph-builder-backend:latest \\
    python /tool/measure_prod6_fix2/measure_fix2.py --pdf-dir /pdf
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, "/tool")
sys.path.insert(0, "/tool/measure_prod6")
sys.path.insert(0, os.environ.get("GRAPH_BUILDER_CODE", "/code"))
import convert as CV  # noqa: E402
import measure as M  # noqa: E402
from src.document_sources.pdf_regions.headings import normalize_text  # noqa: E402

FOLDERS = {"pymupdf4llm": "/tool/measure_prod6/pymupdf4llm",
           "prev": "/tool/measure_prod6/regions_text",
           "fix2": "/tool/measure_prod6_fix2/regions_text"}


def chunks_of(markdown: str, file_name: str):
    tok = os.path.join(CV.CODE, "tokenizers", "ruri-v3-310m", "tokenizer.json")
    if os.path.isfile(tok):
        os.environ.setdefault("CHUNK_TOKENIZER", tok)
    import src.create_chunks as cc
    from langchain_core.documents import Document
    cc.TokenTextSplitter = lambda **kw: None
    doc = Document(page_content=markdown, metadata={"source": file_name, "markdown": True, "markdown_strict": True,
                                                   "title": Path(file_name).stem})
    return cc.CreateChunksofDocument([doc], None).split_file_into_chunks(512, 50, "eval@example.com")


def surge_chunk(markdown: str, file_name: str) -> dict:
    """「3-1 サージ誤差計算」の節のチャンク（section_path に 3-1 を含むもの）の中身"""
    out = []
    for c in chunks_of(markdown, file_name):
        path = json.dumps(c.metadata.get("section_path") or [], ensure_ascii=False)
        if "3-1 サージ誤差計算" in path or "3-1 サージ誤差計算" in c.page_content:
            out.append({"section_path": c.metadata.get("section_path"), "text": c.page_content})
    return {"chunks": out,
            "has_jissoku": any("実測風量" in c["text"] for c in out if "3-1" in json.dumps(c["section_path"], ensure_ascii=False)),
            "has_map": any("マップ風量" in c["text"] for c in out if "3-1" in json.dumps(c["section_path"], ensure_ascii=False)),
            "has_185": any("1.85%" in c["text"] for c in out if "3-1" in json.dumps(c["section_path"], ensure_ascii=False))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf-dir", required=True)
    ap.add_argument("--out", default="/tool/measure_prod6_fix2")
    args = ap.parse_args()
    metrics = {}
    for key, folder in FOLDERS.items():
        name = "metrics_pymupdf4llm.json" if key == "pymupdf4llm" else "metrics_regions.json"
        with open(os.path.join(folder, name), encoding="utf-8") as f:
            metrics[key] = {d["file"]: d for d in json.load(f)["docs"]}
    report = {"docs": {}}
    for path in sorted(glob.glob(os.path.join(args.pdf_dir, "*.pdf"))):
        name = os.path.basename(path)
        stem = Path(name).stem
        ref = CV.reference(path)
        entry = {"labels_top": [(p["page"], lab) for p in ref for lab in p["labels"]], "kinds": [p["kind"] for p in ref], "conv": {}}
        for key, folder in FOLDERS.items():
            with open(os.path.join(folder, f"{stem}.md"), encoding="utf-8") as f:
                md = f.read()
            out_pages = {int(m.group(1)): m.group(2) for m in CV.PAGE_RX.finditer(md)}
            heads = M.headings_of(md)
            nums = [(lvl, M.number_of(t), t) for lvl, t in heads]
            mt = metrics[key].get(name, {})
            e = {"retention": {k: v["ratio"] for k, v in (mt.get("retention") or {}).items()},
                 "admin_leaked": (mt.get("admin_cells") or {}).get("leaked"), "admin_total": (mt.get("admin_cells") or {}).get("total"),
                 "admin_examples": (mt.get("admin_cells") or {}).get("examples"),
                 "annex": [(mt.get("annex") or {}).get("headings"), (mt.get("annex") or {}).get("expected")],
                 "annex_detail": (mt.get("annex") or {}).get("detail"),
                 "headings_count": len(heads),
                 "by_level": dict(sorted(__import__("collections").Counter(l for l, _ in heads).items())),
                 "headings": heads,
                 "numbers_all": [n for _, n, _ in nums if n],
                 "numbers_chsec": [n.replace(".", "-") if n.count(".") else n for _, n, _ in nums if M.is_chapter_section(n)],
                 "lost": {p["page"]: M.lost_chars(p["ref"], out_pages.get(p["page"], "")) for p in ref},
                 "betsu2_h2": [t for lvl, t in heads if lvl == 2 and re.sub(r"\s+", "", normalize_text(t)).startswith("別表2")],
                 "seconds": mt.get("seconds")}
            try:
                e["sections"] = M.sections_detail(md, name)
            except Exception as error:
                e["sections"] = {"error": repr(error)}
            if "3-1 サージ誤差計算" in md:
                e["surge"] = surge_chunk(md, name)
            entry["conv"][key] = e
        base = entry["conv"]["pymupdf4llm"]
        for key in ("prev", "fix2"):
            e = entry["conv"][key]
            e["vs_base_chsec"] = M.seq_compare(base["numbers_chsec"], e["numbers_chsec"])
            e["vs_base_all"] = M.seq_compare(base["numbers_all"], e["numbers_all"])
        entry["prev_vs_fix2_headings"] = M.seq_compare([f"{l}:{t}" for l, t in entry["conv"]["prev"]["headings"]],
                                                       [f"{l}:{t}" for l, t in entry["conv"]["fix2"]["headings"]])
        report["docs"][name] = entry
    with open(os.path.join(args.out, "measure_fix2.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print("written", os.path.join(args.out, "measure_fix2.json"))


if __name__ == "__main__":
    main()
