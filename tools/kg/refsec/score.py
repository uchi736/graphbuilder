"""X18 の採点。要点の語（answer_keys）の照合で機械的に判定し、条件ごとに集計する。

使い方: python3 score.py results.json [overrides.json]
  判定: 正答 = 要点の語がすべて回答に含まれる（NFKC・空白を除いて照合）。一部 = 半数以上。誤答 = それ未満。
  答えの無い問い: 「記載がない」等の断りを含めば正答。
  overrides.json: 人が読んで判定を改めた分 {"<問いの ID>|<条件>": "正答" | "一部" | "誤答"}。改めた理由は台帳に書く。
合格線（事前に固定。方針の案のとおり）:
  経路が要る問いの正答: 現行より 10 問以上多く、かつ半数以上 ／ 対照の正答: 現行から減らない ／
  答えの無い問い: 断る数が減らない ／ 文脈の字数: 現行の 1.2 倍以内 ／ 応答時間: 現行より 1 秒以内の増加
"""
import json
import re
import statistics
import sys
import unicodedata

norm = lambda s: re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(s or "")))
REFUSAL = re.compile(r"記載(が|は)?(ない|ありません|されていない|されていません|見当たら)|見当たりません|含まれていません|含まれていない|わかりません|分かりません|情報がありません|確認できません")


def judge(rec, run):
    answer = run.get("answer") or ""
    if rec["type"] == "unanswerable":
        return "正答" if REFUSAL.search(answer) else "誤答"
    keys = [norm(k) for k in rec.get("answer_keys") or [] if norm(k)]
    hit = sum(1 for k in keys if k in norm(answer))
    if keys and hit == len(keys):
        return "正答"
    return "一部" if keys and hit * 2 >= len(keys) else "誤答"


def main():
    results = json.load(open(sys.argv[1], encoding="utf-8"))["results"]
    overrides = json.load(open(sys.argv[2], encoding="utf-8")) if len(sys.argv) > 2 else {}
    conds = list(results[0]["runs"])
    table = {}
    for rec in results:
        group = "route" if rec["type"].startswith("route") else rec["type"]
        for c in conds:
            run = rec["runs"][c]
            v = overrides.get(f"{rec['id']}|{c}") or judge(rec, run)
            run["verdict"] = v
            t = table.setdefault((group, c), {"n": 0, "正答": 0, "一部": 0, "誤答": 0, "in_ctx": 0, "chars": [], "sec": []})
            t["n"] += 1
            t[v] += 1
            t["in_ctx"] += run["answer_in_context"]
            t["chars"].append(run["context_chars"])
            t["sec"].append(run["elapsed"])
    for sub in ("route_section", "route_document"):
        for c in conds:
            rs = [r for r in results if r["type"] == sub]
            if rs:
                print(f"  {sub:15} {c:14} 正答 {sum(r['runs'][c]['verdict'] == '正答' for r in rs):2}/{len(rs)}  文脈に答え {sum(r['runs'][c]['answer_in_context'] for r in rs):2}")
    print()
    for (g, c), t in sorted(table.items()):
        print(f"{g:13} {c:14} n={t['n']:2} 正答 {t['正答']:2} 一部 {t['一部']:2} 誤答 {t['誤答']:2} 文脈に答え {t['in_ctx']:2} "
              f"字数 中央 {statistics.median(t['chars']):7.0f} 時間 中央 {statistics.median(t['sec']):5.2f}s")
    json.dump({"results": results}, open(sys.argv[1].replace(".json", "_scored.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
