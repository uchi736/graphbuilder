"""X20: 作り直した本番グラフの章・節への参照を、X18 の点検表（refs_review.json の correct_target）と照らして機械的に判定する。

使い方: python3 x20_compare.py <新しい refs_dump.json> <出力 x20_mech.json>
- チャンク ID は sha1(文書名+位置+本文) なので、同じ変換器・同じ分割なら作り直しても変わらない（初めに一致率を数える）。
- 新しい参照のうち、X18 と同じ参照元チャンクと同じ参照の一文（空白を除いて一致）を持つものは、X18 の正しい参照先（correct_target）と
  新しい参照先（targets）を比べて判定する: 参照先が正しい参照先に収まり 1 件以上重なる → 正しい / 重なるが正しくない参照先も含む → 広すぎる /
  重ならない → 誤り。X18 で「正しい参照先なし」とされたもの（correct_target が空）は「要判定」とする。
- X18 に無い参照（gemma4 の参照リンクが新しく作ったもの）は「要判定」として、判定の材料（参照の一文・参照元の本文・参照先の節の本文）を付けて出す。
"""
import json
import re
import sys
import unicodedata

HERE = __file__.rsplit("/", 1)[0]
norm = lambda s: re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(s or "")))


def main():
    new = json.load(open(sys.argv[1], encoding="utf-8"))
    old = json.load(open(f"{HERE}/refs_dump.json", encoding="utf-8"))
    review = json.load(open(f"{HERE}/refs_review.json", encoding="utf-8"))
    old_ids = {c["id"] for c in old["chunks"]}
    new_ids = {c["id"] for c in new["chunks"]}
    text_of = {c["id"]: c for c in new["chunks"]}
    key = lambda r: (r["source"], norm(r.get("quote"))[:80])
    old_by_key = {}
    for i, r in enumerate(old["section_refs"]):
        old_by_key.setdefault(key(r), []).append((r, review["section_refs"][i]))
    out = {"chunk_id_overlap": {"old": len(old_ids), "new": len(new_ids), "same": len(old_ids & new_ids)}, "section_refs": [], "document_refs": []}
    for r in new["section_refs"]:
        rec = {k: r.get(k) for k in ("source", "source_file", "quote", "method", "via", "section_id", "section_title", "section_path", "section_file", "targets")}
        tg = set(r.get("targets") or []) - {r["source"]}
        matches = old_by_key.get(key(r)) or []
        if matches:
            correct = set()
            for _, rv in matches:
                correct |= set(rv.get("correct_target") or [])
            rec["x18_verdicts"] = [rv["verdict"] for _, rv in matches]
            if not correct:
                rec["verdict"] = "要判定"
            elif tg and tg <= correct:
                rec["verdict"] = "正しい"
            elif tg & correct:
                rec["verdict"] = "広すぎる"
            else:
                rec["verdict"] = "誤り"
            rec["judged_by"] = "x18_correct_target"
        else:
            rec["verdict"] = "要判定"
            rec["judged_by"] = None
        if rec["verdict"] == "要判定":
            src = text_of.get(r["source"]) or {}
            rec["material"] = {
                "source_text": (src.get("text") or "")[:1500], "source_heading": src.get("heading"),
                "targets": [{"id": t, "file": (text_of.get(t) or {}).get("fileName"), "heading": (text_of.get(t) or {}).get("heading"),
                             "text": ((text_of.get(t) or {}).get("text") or "")[:900]} for t in sorted(tg)],
            }
        out["section_refs"].append(rec)
    out["document_refs"] = new.get("document_refs", [])
    from collections import Counter
    out["summary"] = {"section_refs": len(out["section_refs"]), "verdicts": dict(Counter(x["verdict"] for x in out["section_refs"])),
                      "via": dict(Counter(x.get("via") for x in out["section_refs"])), "document_refs": len(out["document_refs"])}
    json.dump(out, open(sys.argv[2], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps({"chunk_id_overlap": out["chunk_id_overlap"], **out["summary"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
