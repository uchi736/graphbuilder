# -*- coding: utf-8 -*-
"""実物 6 文書の正解 41 件での採点を、写しのモジュール（src/chunk_reference_rules.py）で再現する。

材料は tools/kg/routes/graph_dump.json の chunks と tools/kg/refs/gold_refs.json。採点の基準は tools/kg/refs/score_refs.py と同じ
（抽出 = 原文の一部の一致、文書 = 参照先の文書の一致、チャンク = 結んだチャンクに accept の語があるか）。LLM もグラフも使わない。

使い方（src と同じ階層の tests から）:
  python3 score_real.py                         # 既定（切り替えられる規則はすべて有効）
  python3 score_real.py --disable heading_name  # 規則を外して採点
  python3 score_real.py --ablation              # 既定と、規則を 1 つずつ・両方外した場合を並べる
  python3 score_real.py --show refs|miss|leftover|history
  python3 score_real.py --json out.json         # 結果を書き出す
"""
import argparse
import collections
import json
import os
import pathlib
import re
import sys
import time
import unicodedata

HERE = pathlib.Path(__file__).resolve().parent
# 写しの src を読む。コンテナの中では CODE_DIR（/code）の下、手元では tests の隣にある
sys.path.insert(0, str((pathlib.Path(os.environ["CODE_DIR"]) if os.environ.get("CODE_DIR") else HERE.parent) / "src"))
import chunk_reference_rules as rules  # noqa: E402

KG = pathlib.Path(os.environ.get("KG_DIR", "/home/uchida/ai-stack/llm-graph-builder/tools/kg"))
sq = lambda s: re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or ""))


def load(chunks_path, gold_path):
    data = json.load(open(chunks_path, encoding="utf-8"))
    chunks = data["chunks"] if isinstance(data, dict) else data
    gold = json.load(open(gold_path, encoding="utf-8"))["references"]
    return chunks, gold


def score(refs, gold, texts):
    """score_refs.py と同じ照合。各正解に、抽出・文書・チャンクの判定を付ける。"""
    used, rows = set(), []
    for g in gold:
        hits = [i for i, r in enumerate(refs) if r["source_doc"] == g["src"] and sq(g["key"]) in sq(r["quote"])]
        used.update(hits)
        row = {"src": g["src"], "key": g["key"], "kind": g["kind"], "level": g["level"], "optional": bool(g.get("optional")),
               "extracted": bool(hits), "doc_ok": False, "chunk": "なし", "links": 0, "links_ok": 0, "reasons": []}
        loose = False
        for i in hits:
            r = refs[i]
            row["doc_ok"] = row["doc_ok"] or r["target_doc"] == g["target"]
            if r.get("reason"):
                row["reasons"].append(r["reason"])
            for t in r["targets"]:
                row["links"] += 1
                text = sq(texts[t["id"]])
                row["links_ok"] += r["target_doc"] == g["target"] and any(sq(a) in text for a in g["accept"])
                loose = loose or (r["target_doc"] == g["target"] and any(sq(a) in text for a in g.get("accept_loose", [])))
        if row["links_ok"]:
            row["chunk"] = "正"
        elif row["links"]:
            row["chunk"] = "近い" if loose else "誤"
        if g["level"] == "chunk":
            row["verdict"] = {"正": "correct", "近い": "wrong", "誤": "wrong", "なし": "unresolved"}[row["chunk"]]
        else:
            row["verdict"] = "correct" if row["doc_ok"] else ("wrong" if any(refs[i]["target_doc"] for i in hits) else "unresolved")
        rows.append(row)
    return rows, used


def summarize(rows):
    need = [r for r in rows if not r["optional"]]
    c = collections.Counter(r["verdict"] for r in need)
    return {"total": len(need), "extracted": sum(r["extracted"] for r in need), "correct": c["correct"], "wrong": c["wrong"], "unresolved": c["unresolved"],
            "chunk_level": {"total": sum(r["level"] == "chunk" for r in need), "correct": sum(r["level"] == "chunk" and r["verdict"] == "correct" for r in need)},
            "doc_level": {"total": sum(r["level"] == "doc" for r in need), "correct": sum(r["level"] == "doc" and r["verdict"] == "correct" for r in need)},
            "links_in_gold_refs": sum(r["links"] for r in need), "links_ok_in_gold_refs": sum(r["links_ok"] for r in need)}


def leftover(result, document_places=True):
    """hybrid で LLM に回るチャンクと理由（chunk_references.plan_llm_work と同じ分け方）。"""
    by_chunk = collections.defaultdict(lambda: collections.Counter())
    for r in result["references"]:
        if r["history"]:
            continue
        if r["reason"]:
            by_chunk[r["source"]]["unresolved"] += 1
        elif r["level"] == "document" and document_places and r.get("places", True):
            by_chunk[r["source"]]["document_only"] += 1
    for cid, info in result["chunks"].items():
        if info.get("llm", info["cue"] and not info["references"]):
            by_chunk[cid]["no_detection"] += 1
    return by_chunk


def run(chunks, gold, disable=()):
    t0 = time.time()
    result = rules.find_references(chunks, disable=disable)
    seconds = time.time() - t0
    texts = {c["id"]: c["text"] for c in chunks}
    rows, used = score(result["references"], gold, texts)
    return result, rows, used, seconds


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chunks", default=str(KG / "routes" / "graph_dump.json"))
    ap.add_argument("--gold", default=str(KG / "refs" / "gold_refs.json"))
    ap.add_argument("--disable", default="", help="外す規則（%s）。カンマ区切り" % ", ".join(rules.OPTIONAL_RULES))
    ap.add_argument("--ablation", action="store_true")
    ap.add_argument("--show", choices=["refs", "miss", "leftover", "history", "extra"])
    ap.add_argument("--json")
    args = ap.parse_args()
    chunks, gold = load(args.chunks, args.gold)
    disable = [x for x in args.disable.split(",") if x]
    result, rows, used, seconds = run(chunks, gold, disable)
    refs = result["references"]
    summary = summarize(rows)
    by_id = {c["id"]: c for c in chunks}
    stats = {"chunks": len(chunks), "references": len(refs), "by_form": dict(collections.Counter(r["form"] for r in refs)),
             "by_level": dict(collections.Counter(r["level"] for r in refs)), "by_stage": dict(collections.Counter(str(r["stage"]) for r in refs)),
             "chunk_links": sum(len(r["targets"]) for r in refs), "document_links": sum(r["level"] == "document" for r in refs),
             "external": sum(r["scope"] == "external" for r in refs), "unresolved": sum(bool(r["reason"]) for r in refs),
             "from_history": sum(r["history"] for r in refs), "links_from_history": sum(len(r["targets"]) + (r["level"] == "document") for r in refs if r["history"]),
             "vague": len(result["vague"]), "dropped": result["dropped"], "seconds": round(seconds, 3)}
    left = leftover(result)
    left_off = leftover(result, document_places=False)
    reasons = collections.Counter()
    for counter in left.values():
        for k in counter:
            reasons[k] += 1
    left_sum = {"chunks_total": len(chunks), "chunks_to_llm": len(left), "chunks_by_reason": dict(reasons),
                "references_by_reason": {k: sum(c[k] for c in left.values()) for k in ("unresolved", "document_only", "no_detection")},
                "chunks_to_llm_without_document_places": len(left_off)}
    print("規則（外した規則: %s）:" % (",".join(disable) or "なし"), json.dumps(summary, ensure_ascii=False))
    print("全体:", json.dumps(stats, ensure_ascii=False))
    print("hybrid で LLM に回る分:", json.dumps(left_sum, ensure_ascii=False))
    for r in rows:
        if r["verdict"] != "correct":
            print("  正しく結べなかった正解 [%s] %-30s 抽出=%s 文書=%s チャンク=%s%s 理由=%s" % (
                r["src"][:12], r["key"][:30], "○" if r["extracted"] else "×", "○" if r["doc_ok"] else "×", r["chunk"], "（任意）" if r["optional"] else "", " / ".join(r["reasons"])[:60]))
    ablation = None
    if args.ablation:
        ablation = {}
        for off in ([], ["heading_name"], ["sub_fallback"], ["heading_name", "sub_fallback"]):
            _, rows2, _, _ = run(chunks, gold, off)
            ablation["+".join(off) or "すべて有効"] = {k: v for k, v in summarize(rows2).items() if k in ("total", "extracted", "correct", "wrong", "unresolved")}
        print("感度:", json.dumps(ablation, ensure_ascii=False))
    if args.show == "refs" or args.show == "history" or args.show == "extra":
        for i, r in enumerate(refs):
            if args.show == "history" and not r["history"]:
                continue
            if args.show == "extra" and (i in used or r["level"] not in ("chunk", "document")):
                continue
            print("[%s#%s] 段%s %-10s %-18s → %s %s %s%s | %s%s\n      %s" % (
                r["source_doc"][:13], r["source_position"], r["stage"], r["form"], (r["locator"] or r["target_document"] or "")[:18], (r["target_doc"] or r["scope"] or "未定")[:13],
                [t["position"] for t in r["targets"]], r["level"], "（履歴）" if r["history"] else "", r["note"], (" 理由: " + r["reason"]) if r["reason"] else "", r["quote"][:100].replace("\n", "⏎")))
    if args.show == "leftover":
        for cid, counter in left.items():
            c = by_id[cid]
            print("[%s#%s] %s" % (c["fileName"][:13], c["position"], dict(counter)))
            for r in refs:
                if r["source"] == cid and not r["history"] and (r["reason"] or r["level"] == "document"):
                    print("      %s | %s | %s" % (r["form"], r["locator"] or r["target_document"], r["reason"] or "文書だけを指す"))
    if args.show == "miss":
        for r in refs:
            if r["reason"]:
                print("[%s#%s] %s | %s | %s%s" % (r["source_doc"][:13], r["source_position"], r["form"], r["locator"] or r["target_document"], r["reason"], "（履歴）" if r["history"] else ""))
    if args.json:
        json.dump({"inputs": {"chunks": args.chunks, "gold": args.gold}, "disabled": disable, "real_gold": summary, "stats": stats, "llm_leftover": left_sum,
                   "ablation": ablation, "per_gold": rows, "references": refs, "catalogue": result["catalogue"]},
                  open(args.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("書き出し:", args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
