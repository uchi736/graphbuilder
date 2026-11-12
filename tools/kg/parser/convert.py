#!/usr/bin/env python3
"""PDF を指定の変換器で Markdown にし、評価の指標を JSON に書く（PDF 変換の置き換えの評価用）。

変換器:
  regions      新方式（backend/src/document_sources/pdf_regions。文字層＋Nemotron Parse＋VLM）。Graph Builder が受け取るのと
               同じ Markdown（graph_builder.build_markdown）を書く
  pymupdf4llm  以前の変換（backend/src/document_sources/pdf_markdown.load_pdf_as_markdown）。比較の相手としてだけ使う
               （中で PyMuPDF・pymupdf4llm を使う。このファイル自体は AGPL の部品を import しない）。
               2026-11-10 に backend から外した。動かすときは pymupdf の入った別イメージ llm-graph-builder-backend:bak-20261110-agpl で、
               控え pdf_markdown.py.bak.agpl・frame_pages.py.bak.agpl をもとの名前で /code/src/document_sources/ に重ねる

指標（文書ごと、ページごと。参照は pypdfium2 の文字層と、新方式と同じ枠の検出で求める）:
  retention     本文の文字の残り。参照の文字（空白を除き、全角・半角をそろえた字の多重集合）のうち出力に残った割合。
                参照は、本文の欄のあるページ（body）は欄の中の文字（pdfium の get_text_bounded）、格子の様式（grid）は枠の中、
                枠の無いページ（none）は上下の端の 7% の帯（柱・ノンブル）を除いたページの文字
  admin_cells   本文の欄のあるページで、欄の外の欄（承認・配布先・来歴など、4 字以上。題名の欄は除く）の文字列が出力に現れた数
  annex         ページ上端の付属のラベル（別表・付表・別紙・添付資料）の数と、見出し（#）になった数
  headings      見出しの数と段（# の数）の分布
  seconds       文書の所要時間、ページごとの所要時間（regions のみ）
  calls         Nemotron Parse・E4B・26B の呼び出し数（regions のみ）
  sections      既存の分割と章・節の層の目録に通したときの付属の節（別表2 などのラベル）。--no-sections で省く

使い方（一時コンテナ。VLM を呼ぶので --network host。メモリ上限付き。動いている backend には入らない）:
  cd ~/ai-stack/llm-graph-builder && docker run --rm --memory=4g --network host -e PYTHONDONTWRITEBYTECODE=1 \\
    -v "$PWD/backend:/code:ro" -v "$PWD/tools/kg/parser:/tool" -v "$PWD/tools/kg/hs/runs/prod_night1109/pdf:/pdf:ro" -w /code \\
    llm-graph-builder-backend:latest python /tool/convert.py --pdf-dir /pdf --converter regions --out /tool/out_impl \\
    --files VTR-T005B.pdf VTR-T004C.pdf
"""
from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

CODE = os.environ.get("GRAPH_BUILDER_CODE", "/code")
sys.path.insert(0, CODE)

from src.document_sources.pdf_regions import frame as FR  # noqa: E402
from src.document_sources.pdf_regions import pdfpage  # noqa: E402
from src.document_sources.pdf_regions.headings import header_annex, normalize_text, doc_number_keys  # noqa: E402

MARGIN = 0.07
PAGE_RX = re.compile(r"<!-- page:(\d+) -->\n(.*?)(?=\n\n<!-- page:|\Z)", re.S)
HEADING_RX = re.compile(r"(?m)^(#{1,6})[ \t]+(.+?)\s*$")
ANNEX_START_RX = re.compile(r"^\s*(別\s*表|別\s*紙|付\s*表|附\s*表|付\s*図|附\s*図|別\s*図|添\s*付\s*資\s*料)\s*[-‐−－]?\s*(\d{1,2})?(?!\d)")
ANNEX_HEAD_RX = re.compile(r"(別\s*表|別\s*紙|付\s*表|附\s*表|付\s*図|附\s*図|別\s*図|添\s*付\s*資\s*料)")


LEADER = re.compile(r"[・･.．…‥]{3,}")


def bag(text: str) -> Counter:
    """空白と点線（リーダー。新方式は点線だけの行を本文に入れない）を除いた字の多重集合"""
    return Counter(re.sub(r"\s+", "", LEADER.sub("", normalize_text(text or ""))))


def squeeze(text: str) -> str:
    return re.sub(r"\s+", "", normalize_text(text or ""))


def kept(ref: Counter, out: Counter) -> int:
    return sum(min(c, out[k]) for k, c in ref.items())


def reference(pdf_path: str) -> list:
    """ページごとの参照: 区分、参照の文字、欄の外の欄の文字列、上端の付属のラベル"""
    pages = []
    doc = pdfpage.open_document(pdf_path)
    own = doc_number_keys(Path(pdf_path).stem)
    try:
        for i in range(len(doc)):
            page = doc[i]
            W, H = pdfpage.page_size(page)
            layer = pdfpage.text_layer(page)
            frame = FR.detect_page_frame(layer, *pdfpage.line_segments(page))
            admin, title = [], None
            if frame.kind == "body":
                ref_text = pdfpage.bounded_text(page, frame.body)
                title = FR.frame_title(layer, frame)
                bx0, by0, bx1, by1 = frame.body
                if frame.virtual:
                    # 枠の線が無い様式: 本文の範囲の外の行のかけらを管理の欄とみなす
                    outside = [flat for r, flat, _ in FR._pieces(layer)
                               if not (bx0 <= (r[0] + r[2]) / 2 <= bx1 and by0 <= (r[1] + r[3]) / 2 <= by1)]
                else:
                    pool = [ch.i for ch in layer.chars if ch.ink and not (bx0 <= ch.cx <= bx1 and by0 <= ch.cy <= by1)]
                    outside = [text for _, text in FR.cell_texts(layer, frame, pool)]
                for text in outside:
                    t = squeeze(text)
                    if len(t) >= 4 and (not title or t not in squeeze(title)):
                        admin.append(t)
            elif frame.kind == "grid":
                ref_text = pdfpage.bounded_text(page, frame.outer)
            else:
                ref_text = pdfpage.bounded_text(page, (0, MARGIN * H, W, (1 - MARGIN) * H))
            # ページ上端（15%）の付属のラベル: 柱だけのラベル（別 表 １、HS-VTR-T003C 別紙-1）と、題の行の頭のラベル（別表2過給機…）。
            # 他の文書の番号を伴うラベル（QC-KAI-001 別紙3）は数えない
            labels = []
            for r, flat, text in FR._pieces(layer):
                if r[1] > 0.15 * H:
                    continue
                lab = header_annex(text, own)
                if not lab:
                    m = ANNEX_START_RX.match(normalize_text(text))
                    if m and not (doc_number_keys(text) and not (doc_number_keys(text) & own)):
                        lab = re.sub(r"\s+", "", m.group(1)) + (m.group(2) or "")
                if lab:
                    labels.append(lab)
            pages.append({"page": i + 1, "kind": frame.kind, "ref": ref_text, "admin": admin, "labels": sorted(set(labels))})
            page.close()
    finally:
        doc.close()
    return pages


def sections_of(markdown: str, file_name: str) -> dict:
    """既存の分割と章・節の層の目録に通し、付属の節（ラベル）と章の番号を返す"""
    tok = os.path.join(CODE, "tokenizers", "ruri-v3-310m", "tokenizer.json")
    if os.path.isfile(tok):
        os.environ.setdefault("CHUNK_TOKENIZER", tok)
    import src.create_chunks as cc
    from langchain_core.documents import Document
    from src import document_layer_rules as R
    from src import section_layer as SL
    cc.TokenTextSplitter = lambda **kw: None   # Markdown の経路では使わない（gpt2 の語彙を取りに外へ出ないように）
    doc = Document(page_content=markdown, metadata={"source": file_name, "markdown": True, "markdown_strict": True,
                                                   "title": Path(file_name).stem})
    chunks = cc.CreateChunksofDocument([doc], None).split_file_into_chunks(512, 50, "eval@example.com")
    rows = [{"id": f"c{i}", "fileName": file_name, "position": i + 1, "section": c.metadata.get("section"),
             "section_path": json.dumps(c.metadata.get("section_path") or [], ensure_ascii=False), "text": c.page_content,
             "page_number": c.metadata.get("page_number")} for i, c in enumerate(chunks)]
    secs, _ = SL.sections_from_catalogue(rows, R.load_config())
    idx = SL.SectionIndex(secs)
    return {"chunks": len(chunks),
            "annex_labels": sorted({lab for (f, lab) in idx.annex if f == file_name}),
            "chapters": sorted({s["number"] for s in secs.values() if s.get("form") == "章" and s.get("number")}, key=lambda x: int(x)),
            "sections": len(secs)}


def evaluate(markdown: str, ref_pages: list, file_name: str, with_sections: bool) -> dict:
    out_pages = {int(m.group(1)): m.group(2) for m in PAGE_RX.finditer(markdown)}
    totals = {k: [0, 0] for k in ("body", "grid", "none", "all")}
    per_page, admin_total, admin_leaked, leaked_examples = [], 0, 0, []
    for p in ref_pages:
        ref = bag(p["ref"])
        out_text = out_pages.get(p["page"], "")
        n_ref = sum(ref.values())
        n_kept = kept(ref, bag(out_text))
        for k in (p["kind"], "all"):
            totals[k][0] += n_ref
            totals[k][1] += n_kept
        flat = squeeze(out_text)
        leaks = [a for a in p["admin"] if a in flat]
        admin_total += len(p["admin"])
        admin_leaked += len(leaks)
        leaked_examples += [f"p{p['page']}: {a[:30]}" for a in leaks[:3]]
        per_page.append({"page": p["page"], "kind": p["kind"], "ref_chars": n_ref,
                         "retention": round(n_kept / n_ref, 4) if n_ref else None, "admin_leaked": len(leaks)})
    retention = {k: {"ref_chars": v[0], "kept": v[1], "ratio": round(v[1] / v[0], 4) if v[0] else None} for k, v in totals.items()}
    heads = [(len(m.group(1)), m.group(2)) for m in HEADING_RX.finditer(markdown)]
    expected = [(p["page"], lab) for p in ref_pages for lab in p["labels"]]
    found = []
    for page, lab in expected:
        text = out_pages.get(page, "")
        hit = [h for lvl, h in ((len(m.group(1)), m.group(2)) for m in HEADING_RX.finditer(text)) if squeeze(h).startswith(lab)]
        found.append({"page": page, "label": lab, "heading": hit[0] if hit else None})
    result = {"retention": retention, "per_page": per_page,
              "admin_cells": {"total": admin_total, "leaked": admin_leaked, "examples": leaked_examples[:10]},
              "annex": {"expected": len(expected), "headings": sum(1 for f in found if f["heading"]), "detail": found},
              "headings": {"count": len(heads), "by_level": {str(k): v for k, v in sorted(Counter(l for l, _ in heads).items())},
                           "annex_headings": [h for l, h in heads if ANNEX_HEAD_RX.match(h)]}}
    if with_sections:
        try:
            result["sections"] = sections_of(markdown, file_name)
        except Exception as error:  # 評価の補助なので、失敗しても変換の結果は残す
            result["sections"] = {"error": str(error)}
    return result


def run_regions(path: str, args):
    from src.document_sources.pdf_regions.converter import RegionsConfig, convert_pdf
    from src.document_sources.pdf_regions.graph_builder import build_markdown
    cfg = RegionsConfig(parse_url=args.parse_url, parse_model=args.parse_model, triage_url=args.triage_url,
                        triage_model=args.triage_model, vlm_url=args.vlm_url, vlm_model=args.vlm_model, figures=args.figures,
                        timeout=args.timeout, retries=args.retries, parse_concurrency=args.parse_concurrency,
                        vlm_concurrency=args.vlm_concurrency, title_line=not args.no_title)
    res = convert_pdf(path, cfg)
    if res is None:
        return None, {"failed": True}
    stats = dict(res.stats)
    calls = {"nemotron": stats.get("parse_calls"), "e4b": stats.get("triage_calls"), "26b": stats.get("describe_calls")}
    pages = [{"page": p["page"], "seconds": p.get("seconds"), "kind": p.get("kind"), "fallback": p.get("fallback"),
              "regions": p.get("regions"), "tables_gfm": p.get("tables_gfm"), "tables_to_text": p.get("tables_to_text"),
              "figures": p.get("figures"), "described": p.get("described"), "vlm_errors": p.get("vlm_errors"),
              "orphan_chars": p.get("orphan_chars")} for p in stats.get("page_stats", [])]
    return build_markdown(res), {"calls": calls, "page_seconds": pages, "stats": stats}


def run_pymupdf4llm(path: str, args):
    from src.document_sources.pdf_markdown import load_pdf_as_markdown   # 今の変換（比較の相手）
    docs = load_pdf_as_markdown(path)
    if not docs:
        return None, {"failed": True}
    return docs[0].page_content, {}


def _load_previous(out_dir: str, stem: str):
    path = os.path.join(out_dir, f"{stem}.md")
    if not os.path.isfile(path):
        return None, {"failed": True, "error": "no previous output"}
    with open(path, encoding="utf-8") as f:
        md = f.read()
    extra = {}
    stats_path = os.path.join(out_dir, f"{stem}.stats.json")
    if os.path.isfile(stats_path):
        with open(stats_path, encoding="utf-8") as f:
            stats = json.load(f)
        extra = {"calls": {"nemotron": stats.get("parse_calls"), "e4b": stats.get("triage_calls"), "26b": stats.get("describe_calls")},
                 "page_seconds": [{"page": p["page"], "seconds": p.get("seconds"), "kind": p.get("kind")} for p in stats.get("page_stats", [])],
                 "stats": stats}
    return md, extra


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--pdf-dir", required=True)
    ap.add_argument("--converter", choices=["regions", "pymupdf4llm"], required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--files", nargs="*", help="対象のファイル名（省略時はディレクトリの *.pdf すべて）")
    ap.add_argument("--parse-url", default="http://localhost:8022/v1")
    ap.add_argument("--parse-model", default="nemotron-parse")
    ap.add_argument("--triage-url", default="http://localhost:8030/v1")
    ap.add_argument("--triage-model", default="google/gemma-4-E4B-it")
    ap.add_argument("--vlm-url", default="http://localhost:8000/v1")
    ap.add_argument("--vlm-model", default="google/gemma-4-26B-A4B-it")
    ap.add_argument("--figures", default="describe", choices=["describe", "text", "off"])
    ap.add_argument("--timeout", type=float, default=180.0)
    ap.add_argument("--retries", type=int, default=2)
    ap.add_argument("--parse-concurrency", type=int, default=2)
    ap.add_argument("--vlm-concurrency", type=int, default=2)
    ap.add_argument("--no-title", action="store_true", help="枠の題名の欄を先頭に置かない")
    ap.add_argument("--no-sections", action="store_true", help="章・節の層の目録の確認を省く")
    ap.add_argument("--metrics-only", action="store_true", help="変換せず、--out にある <名前>.md（と .stats.json）から指標だけを出し直す")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    os.makedirs(args.out, exist_ok=True)
    names = args.files or sorted(os.path.basename(p) for p in glob.glob(os.path.join(args.pdf_dir, "*.pdf")))
    report = {"converter": args.converter, "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
              "config": {k: v for k, v in vars(args).items() if k not in ("files",)}, "docs": []}
    for name in names:
        path = os.path.join(args.pdf_dir, name)
        stem = Path(name).stem
        t0 = time.time()
        if args.metrics_only:
            md, extra = _load_previous(args.out, stem)
            seconds = (extra.get("stats") or {}).get("seconds", 0.0)
        else:
            try:
                md, extra = (run_regions if args.converter == "regions" else run_pymupdf4llm)(path, args)
            except Exception as error:
                logging.exception("%s: conversion raised", name)
                md, extra = None, {"failed": True, "error": str(error)}
            seconds = round(time.time() - t0, 2)
        entry = {"file": name, "seconds": seconds}
        entry.update({k: v for k, v in extra.items() if k != "stats"})
        if md is not None:
            if not args.metrics_only:
                with open(os.path.join(args.out, f"{stem}.md"), "w", encoding="utf-8") as f:
                    f.write(md)
            if "stats" in extra and not args.metrics_only:
                with open(os.path.join(args.out, f"{stem}.stats.json"), "w", encoding="utf-8") as f:
                    json.dump(extra["stats"], f, ensure_ascii=False, indent=1)
            ref = reference(path)
            entry["pages"] = len(ref)
            entry.update(evaluate(md, ref, name, not args.no_sections))
        report["docs"].append(entry)
        r = entry.get("retention", {})
        logging.info("%s: %.1fs retention body=%s none=%s all=%s annex %s/%s admin leaked %s", name, seconds,
                     (r.get("body") or {}).get("ratio"), (r.get("none") or {}).get("ratio"), (r.get("all") or {}).get("ratio"),
                     (entry.get("annex") or {}).get("headings"), (entry.get("annex") or {}).get("expected"),
                     (entry.get("admin_cells") or {}).get("leaked"))
    tot = {k: [0, 0] for k in ("body", "grid", "none", "all")}
    for d in report["docs"]:
        for k, v in (d.get("retention") or {}).items():
            tot[k][0] += v["ref_chars"]
            tot[k][1] += v["kept"]
    report["summary"] = {
        "docs": len(report["docs"]), "failed": [d["file"] for d in report["docs"] if d.get("failed")],
        "retention": {k: round(v[1] / v[0], 4) if v[0] else None for k, v in tot.items()},
        "annex": [sum((d.get("annex") or {}).get("headings", 0) for d in report["docs"]),
                  sum((d.get("annex") or {}).get("expected", 0) for d in report["docs"])],
        "admin_cells_leaked": [sum((d.get("admin_cells") or {}).get("leaked", 0) for d in report["docs"]),
                               sum((d.get("admin_cells") or {}).get("total", 0) for d in report["docs"])],
        "seconds": round(sum(d.get("seconds", 0) for d in report["docs"]), 1),
        "calls": {k: sum(((d.get("calls") or {}).get(k) or 0) for d in report["docs"]) for k in ("nemotron", "e4b", "26b")},
    }
    with open(os.path.join(args.out, f"metrics_{args.converter}.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print(json.dumps(report["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
