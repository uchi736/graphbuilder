#!/usr/bin/env python3
"""本番 6 文書の計測の追加集計（convert.py の指標に足すもの）。

convert.py が書いた 3 通りの出力（pymupdf4llm・regions_describe・regions_text）の Markdown を読み、次を求める。
  - 文字層の全ページから付属のラベルの語（別表・付表・別紙・添付資料）を含むかけらを洗い出す（位置つき）
  - ページごとの失った字（参照の字の多重集合から出力の字を引いた残り）
  - 見出しの一覧と節番号の並び（1, 1-1, 1), a) …）。今の変換の並びとの一致（difflib）
  - 既存の分割（CreateChunksofDocument）と章・節の層の目録（sections_from_catalogue）の詳細:
    チャンク数、節の数と形の内訳、付属の節（ラベル）と、別表2 の節の有無と子の節

使い方（一時コンテナ。外へは出ない）:
  cd ~/ai-stack/llm-graph-builder && docker run --rm --memory=4g --network none -e PYTHONDONTWRITEBYTECODE=1 \\
    -v "$PWD/backend:/code:ro" -v "$PWD/tools/kg/parser:/tool:ro" -v "$PWD/tools/kg/parser/measure_prod6:/out" \\
    -v "$PWD/tools/kg/hs/runs/prod_night1109/pdf:/pdf:ro" -w /code llm-graph-builder-backend:latest \\
    python /out/measure.py --pdf-dir /pdf --out /out
"""
from __future__ import annotations

import argparse
import collections
import difflib
import glob
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, "/tool")
sys.path.insert(0, os.environ.get("GRAPH_BUILDER_CODE", "/code"))
import convert as CV  # noqa: E402
from src.document_sources.pdf_regions import pdfpage  # noqa: E402
from src.document_sources.pdf_regions import frame as FR  # noqa: E402
from src.document_sources.pdf_regions.headings import normalize_text  # noqa: E402

CONVERTERS = [("pymupdf4llm", "pymupdf4llm"), ("regions_describe", "regions"), ("regions_text", "regions")]
ANNEX_ANY = re.compile(r"(別\s*表|別\s*紙|付\s*表|附\s*表|添\s*付\s*資\s*料)")
HEADING_RX = re.compile(r"(?m)^(#{1,6})[ \t]+(.+?)\s*$")
# 節番号: 「1.」「1-1」「3-1 .」「3.1」「3.1.2」「1)」「a)」「(1)」
NUM_RX = [
    ("dotted", re.compile(r"^(\d{1,2}(?:\.\d{1,2}){1,3})(?=[\s　]|[^\d.])")),
    ("hyphen", re.compile(r"^(\d{1,2}(?:\s*[-‐−－]\s*\d{1,2}){1,3})(?!\d)")),
    ("chapter", re.compile(r"^(\d{1,2})\s*[.．](?!\d)")),
    ("paren", re.compile(r"^(\d{1,2}|[a-zA-Z])\)")),
    ("paren2", re.compile(r"^\((\d{1,2}|[a-zA-Z])\)")),
]


def number_of(title: str):
    t = normalize_text(title).strip()
    for kind, rx in NUM_RX:
        m = rx.match(t)
        if m:
            n = re.sub(r"\s+", "", m.group(1))
            n = re.sub(r"[‐−－]", "-", n)
            if kind == "paren":
                return n + ")"
            if kind == "paren2":
                return "(" + n + ")"
            return n
    return None


def is_chapter_section(num: str) -> bool:
    return bool(num) and bool(re.fullmatch(r"\d{1,2}(?:[-.]\d{1,2})*", num))


def annex_scan(pdf_path: str) -> list:
    """文字層の全ページの、付属のラベルの語を含むかけら（ページ、上端からの位置の割合、文字列）"""
    found = []
    doc = pdfpage.open_document(pdf_path)
    try:
        for i in range(len(doc)):
            page = doc[i]
            W, H = pdfpage.page_size(page)
            layer = pdfpage.text_layer(page)
            for r, flat, text in FR._pieces(layer):
                if ANNEX_ANY.search(normalize_text(text)):
                    found.append({"page": i + 1, "y": round(r[1] / H, 3), "text": text.strip()[:80]})
            page.close()
    finally:
        doc.close()
    return found


def lost_chars(ref_text: str, out_text: str) -> str:
    ref, out = CV.bag(ref_text), CV.bag(out_text)
    lost = ref - out
    return " ".join(f"{ch}×{n}" if n > 1 else ch for ch, n in lost.most_common())


def sections_detail(markdown: str, file_name: str) -> dict:
    tok = os.path.join(CV.CODE, "tokenizers", "ruri-v3-310m", "tokenizer.json")
    if os.path.isfile(tok):
        os.environ.setdefault("CHUNK_TOKENIZER", tok)
    import src.create_chunks as cc
    from langchain_core.documents import Document
    from src import document_layer_rules as R
    from src import section_layer as SL
    cc.TokenTextSplitter = lambda **kw: None
    doc = Document(page_content=markdown, metadata={"source": file_name, "markdown": True, "markdown_strict": True,
                                                   "title": Path(file_name).stem})
    chunks = cc.CreateChunksofDocument([doc], None).split_file_into_chunks(512, 50, "eval@example.com")
    rows = [{"id": f"c{i}", "fileName": file_name, "position": i + 1, "section": c.metadata.get("section"),
             "section_path": json.dumps(c.metadata.get("section_path") or [], ensure_ascii=False), "text": c.page_content,
             "page_number": c.metadata.get("page_number")} for i, c in enumerate(chunks)]
    secs, chunk_secs = SL.sections_from_catalogue(rows, R.load_config())
    idx = SL.SectionIndex(secs)
    forms = collections.Counter(s.get("form") for s in secs.values())
    annex = {}
    for (f, lab), sids in idx.annex.items():
        if f != file_name:
            continue
        sid = sids[0]
        children = [s for s in secs.values() if s.get("parent") == sid]
        annex[lab] = {"title": secs[sid].get("title"), "source": secs[sid].get("source"),
                      "children": [c.get("number") or c.get("title") for c in children]}
    roots = [{"title": s.get("title"), "form": s.get("form"), "number": s.get("number"), "control": s.get("control"),
              "children": sum(1 for c in secs.values() if c.get("parent") == s["id"])}
             for s in sorted(secs.values(), key=lambda s: (s.get("first_position") or 0, s.get("offset") or 0)) if not s.get("parent")]
    first_title = None
    for c in chunks[:1]:
        first_title = c.metadata.get("section_path")
    return {"chunks": len(chunks), "sections": len(secs), "forms": dict(forms), "annex": annex,
            "has_annex_2": "別表2" in annex, "roots": roots,
            "chunks_with_section": len(chunk_secs),
            "chunk_tokens": [c.metadata.get("tokens") or None for c in chunks][:0]}


def headings_of(markdown: str) -> list:
    return [(len(m.group(1)), m.group(2).strip()) for m in HEADING_RX.finditer(markdown)]


def seq_compare(base: list, other: list) -> dict:
    sm = difflib.SequenceMatcher(a=base, b=other, autojunk=False)
    same = sum(b.size for b in sm.get_matching_blocks())
    ops = [(tag, base[i1:i2], other[j1:j2]) for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != "equal"]
    return {"base": len(base), "other": len(other), "matched": same, "identical": base == other,
            "diff": [{"op": t, "only_base": a, "only_other": b} for t, a, b in ops]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf-dir", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    names = sorted(os.path.basename(p) for p in glob.glob(os.path.join(args.pdf_dir, "*.pdf")))
    report = {"docs": {}}
    for name in names:
        stem = Path(name).stem
        path = os.path.join(args.pdf_dir, name)
        ref = CV.reference(path)
        entry = {"annex_scan": annex_scan(path),
                 "labels_top": [(p["page"], lab) for p in ref for lab in p["labels"]],
                 "kinds": [p["kind"] for p in ref], "conv": {}}
        mds = {}
        for folder, _ in CONVERTERS:
            md_path = os.path.join(args.out, folder, f"{stem}.md")
            if not os.path.isfile(md_path):
                continue
            with open(md_path, encoding="utf-8") as f:
                md = f.read()
            mds[folder] = md
            out_pages = {int(m.group(1)): m.group(2) for m in CV.PAGE_RX.finditer(md)}
            heads = headings_of(md)
            nums = [(lvl, number_of(t), t) for lvl, t in heads]
            e = {"headings": heads,
                 "numbers_all": [n for _, n, _ in nums if n],
                 "numbers_chsec": [n.replace(".", "-") if n.count(".") else n for _, n, _ in nums if is_chapter_section(n)],
                 "lost": {p["page"]: lost_chars(p["ref"], out_pages.get(p["page"], "")) for p in ref},
                 "betsu2_h2": [t for lvl, t in heads if lvl == 2 and re.sub(r"\s+", "", normalize_text(t)).startswith("別表2")],
                 "betsu2_any": [(lvl, t) for lvl, t in heads if re.sub(r"\s+", "", normalize_text(t)).startswith("別表2")]}
            try:
                e["sections"] = sections_detail(md, name)
            except Exception as error:
                e["sections"] = {"error": repr(error)}
            entry["conv"][folder] = e
        base = entry["conv"].get("pymupdf4llm")
        for folder in ("regions_describe", "regions_text"):
            e = entry["conv"].get(folder)
            if base and e:
                e["vs_base_chsec"] = seq_compare(base["numbers_chsec"], e["numbers_chsec"])
                e["vs_base_all"] = seq_compare(base["numbers_all"], e["numbers_all"])
        if "regions_describe" in mds and "regions_text" in mds:
            entry["describe_vs_text_identical_headings"] = (entry["conv"]["regions_describe"]["headings"] == entry["conv"]["regions_text"]["headings"])
        report["docs"][name] = entry
    with open(os.path.join(args.out, "measure_extra.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print("written", os.path.join(args.out, "measure_extra.json"))


if __name__ == "__main__":
    main()
