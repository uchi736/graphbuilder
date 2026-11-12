"""X24 の集計。eval_synth.py の結果（results_x24.json）から、型 × 条件の指標、一覧選択が働いた件数、応答時間、問いごとの差を出す。

使い方: python3 analyze_x24.py results_x24.json [manual_judge.json]
  manual_judge.json … 条件を伏せて付けた内容判定（{"<qid>|<条件>": "正答|一部|誤答"}）。あれば内容判定の正答数も出す。
"""
import json
import statistics
import sys

A, B = "参照追跡", "参照追跡・一覧"
DOC_ONLY = {"C1-03", "C1-05", "C3-03", "C3-06", "C4-01", "C4-03", "C4-05", "C5-04", "C7-01", "C7-04", "C7-06", "C8-01", "C8-04", "CORE-02"}


def group(rec):
    return "control" if rec["type"] == "control" else rec["type"]


def main():
    out = json.load(open(sys.argv[1], encoding="utf-8"))["questions"]
    manual = json.load(open(sys.argv[2], encoding="utf-8")) if len(sys.argv) > 2 else {}
    rows = {}
    for rec in out:
        g = group(rec)
        for key in (g, "route" if g != "control" else None, "doc_only" if rec["qid"] in DOC_ONLY else None):
            if not key:
                continue
            for name in (A, B):
                run = rec["runs"].get(name)
                if not run:
                    continue
                a = rows.setdefault((key, name), {"n": 0, "answer_in": 0, "correct": 0, "confused": 0, "manual_ok": 0, "manual_part": 0, "secs": []})
                a["n"] += 1
                a["answer_in"] += run["answer_in"]
                a["correct"] += bool(run.get("correct"))
                a["confused"] += bool(run.get("confused"))
                m = manual.get(f"{rec['qid']}|{name}")
                a["manual_ok"] += m == "正答"
                a["manual_part"] += m == "一部"
                a["secs"].append(run["sec"])
    print("== 型 × 条件（n・答えのチャンクが文脈に・道具の正答・取り違え" + ("・内容判定の正答／一部" if manual else "") + "・応答時間の中央値）")
    for (key, name), a in sorted(rows.items()):
        print(f"{key:9s} {name:8s} n={a['n']:3d} 到達 {a['answer_in']:3d} 正答 {a['correct']:3d} 取り違え {a['confused']:2d}"
              + (f" 内容 {a['manual_ok']:3d}/{a['manual_part']:2d}" if manual else "") + f" 時間 {statistics.median(a['secs']):.1f}s")

    print("\n== 一覧選択の働き（条件 B の応答の references）")
    worked_q, fb_q, sl_entries, fb_entries, picks, reasons = set(), set(), 0, 0, [], {}
    for rec in out:
        run = rec["runs"].get(B)
        if not run:
            continue
        for e in run.get("references") or []:
            if e.get("selection") == "summary_list":
                worked_q.add(rec["qid"])
                sl_entries += 1
                picks.append(len(e.get("selected_sections") or []))
            elif e.get("selection") == "rerank":
                fb_q.add(rec["qid"])
                fb_entries += 1
                reasons[e.get("selection_fallback")] = reasons.get(e.get("selection_fallback"), 0) + 1
    print(f"一覧選択で足した参照 {sl_entries} 件（問い {len(worked_q)} 問）、選んだ節の数の分布 {sorted(picks)}")
    print(f"rerank に戻した参照 {fb_entries} 件（問い {len(fb_q)} 問）、理由 {reasons}")
    print("一覧選択が働いた問い:", sorted(worked_q))
    print("戻した問い:", sorted(fb_q))

    print("\n== 問いごとの差（文脈のチャンクが違う・到達が違う・道具の正答が違う）")
    ctx_diff = 0
    for rec in out:
        ra, rb = rec["runs"].get(A), rec["runs"].get(B)
        if not (ra and rb):
            continue
        ida = {e["chunk_id"] for e in ra.get("references") or []}
        idb = {e["chunk_id"] for e in rb.get("references") or []}
        diff_ctx = ida != idb
        ctx_diff += diff_ctx
        if diff_ctx or ra["answer_in"] != rb["answer_in"] or bool(ra.get("correct")) != bool(rb.get("correct")):
            print(f"{rec['qid']:8s} {rec['type']:8s} 参照先が違う={int(diff_ctx)} 到達 {int(ra['answer_in'])}→{int(rb['answer_in'])} 正答 {int(bool(ra.get('correct')))}→{int(bool(rb.get('correct')))}"
                  + (f" 内容 {manual.get(rec['qid'] + '|' + A, '-')}→{manual.get(rec['qid'] + '|' + B, '-')}" if manual else ""))
    print(f"参照先の集合が違う問い {ctx_diff} / {len(out)}")


if __name__ == "__main__":
    main()
