# -*- coding: utf-8 -*-
"""文書台帳の属性（正式名称・種類・略称・旧名称・旧番号・現行版）と参照の一文の解決の内訳を、グラフの書き出し（graph_dump*.json）から数える。
グラフにも LLM にも触らない（backend の plan_document_layer を呼ぶだけ）。標準ライブラリだけで動く。

使い方（ai-stack/llm-graph-builder で）:
    python3 tools/kg/registry/count_ledger_fields.py tools/kg/hs/runs/prod_parser1110/graph_dump.json
    python3 tools/kg/registry/count_ledger_fields.py tools/kg/synth/graph_dump7_edc_fold.json --config tools/kg/registry/synth_numbers.json
    --json 出力先  で計画の要約と Standard ごとの属性を JSON に書く
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(ROOT, "backend"))
from src import document_layer_rules as R  # noqa: E402
from src.document_layer import plan_document_layer  # noqa: E402


def load_dump(path):
    d = json.load(open(path, encoding="utf-8"))
    rows = [{"id": c["id"], "fileName": c["fileName"], "position": c["position"], "text": c["text"], "section": c.get("section")} for c in d["chunks"]]
    file_of = {c["id"]: c["fileName"] for c in d["chunks"]}
    links = [{"src": r["source"], "src_doc": file_of.get(r["source"]), "tgt_doc": file_of.get(r["target"]), "quote": r.get("quote")}
             for r in d.get("refers_to", []) if file_of.get(r["source"]) and file_of.get(r["target"]) and file_of[r["source"]] != file_of[r["target"]]]
    ents, rule_std = [], []
    for e in d.get("entities", []):
        p = e.get("props", {})
        if "Document" in e["labels"] or "Chunk" in e["labels"] or not p.get("id"):
            continue
        if p.get("method") == "rule":
            if "Standard" in e["labels"]:
                rule_std.append({"id": p["id"], "eid": e["id"], "aliases": p.get("aliases") or [], "external": p.get("external")})
            continue
        ents.append({"id": p["id"], "labels": ["__Entity__"] + e["labels"], "eid": e["id"], "description": p.get("description")})
    return rows, links, ents, rule_std


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dump")
    ap.add_argument("--config")
    ap.add_argument("--json")
    a = ap.parse_args()
    cfg = R.load_config(a.config)
    rows, links, ents, rule_std = load_dump(a.dump)
    plan = plan_document_layer(rows, cfg, chunk_links=links, entities=ents, rule_standards=rule_std)
    s = plan["summary"]
    print(json.dumps({k: s[k] for k in ("documents", "is_version_of", "refers_to", "refers_to_alias", "merge_candidates", "merge_rule_variants",
                                         "current_versions", "current_not_latest", "ledger_fields")}, ensure_ascii=False, indent=1))
    print(json.dumps(s["resolution"], ensure_ascii=False, indent=1))
    for no, v in sorted(plan["standards"].items()):
        extra = {k: sorted(v[k]) for k in ("abbreviations", "former_names", "former_numbers") if v[k]}
        if extra or v["aliases"]:
            print(" ", no, v.get("kind"), v.get("official_name"), extra, ("aliases=" + ",".join(sorted(v["aliases"]))) if v["aliases"] else "")
    if plan["resolution_detail"]["unresolved_abbreviations"]:
        print("  文書らしい語の略称で台帳に無いもの:", plan["resolution_detail"]["unresolved_abbreviations"][:10])
    if a.json:
        out = {"summary": s, "current": plan["current"],
               "standards": {no: {k: (sorted(v[k]) if isinstance(v[k], set) else v[k]) for k in ("official_name", "kind", "abbreviations", "former_names",
                                                                                                "former_numbers", "aliases", "external", "enacted", "latest_rev")}
                             for no, v in plan["standards"].items()},
               "resolution_detail": plan["resolution_detail"]}
        json.dump(out, open(a.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)


if __name__ == "__main__":
    main()
