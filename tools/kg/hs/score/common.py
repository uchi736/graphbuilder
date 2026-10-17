# 書き出し（graph_dump.json）と正解グラフを読む共通部品。標準ライブラリのみ。
# scratchpad の methods-analysis/audit/common.py の写しを、書き出しの場所と文書の絞り込みを引数で選べるように直したもの。
import json, re, unicodedata, collections
K = "/home/uchida/ai-stack/llm-graph-builder/tools/kg"

def norm(s):
    """score_gold.py と同じ正規化: NFKC、（最新版）等の除去、空白除去、大文字化"""
    s = unicodedata.normalize("NFKC", s or "")
    s = re.sub(r"[（(](最新版|旧版|旧|改|案|参考)[)）]", "", s)
    return re.sub(r"\s+", "", s).upper()

def norm2(s):
    """数え直し用: norm に加えて、ハイフン類と波ダッシュ類をそろえる"""
    s = norm(s)
    s = re.sub(r"[‐‑‒–—―−－ｰ]", "-", s)
    return s.replace("〜", "~").replace("～", "~")

def load_dump(path, files=None):
    """書き出しを読む。files を渡すと、その文書のチャンクと、それに付くエンティティ、その間のリレーションだけに絞る。"""
    d = json.load(open(path, encoding="utf-8"))
    if files is not None:
        files = set(files)
        chunks = [c for c in d["chunks"] if c["fileName"] in files]
        cid = {c["id"] for c in chunks}
        has = [h for h in d["has_entity"] if h["chunk"] in cid]
        eid = {h["entity"] for h in has}
        d = {"chunks": chunks, "has_entity": has, "refers_to": [r for r in d.get("refers_to", []) if r["source"] in cid and r["target"] in cid],
             "entities": [e for e in d["entities"] if e["id"] in eid],
             "relationships": [r for r in d["relationships"] if r["source"] in eid and r["target"] in eid]}
    ents = {e["id"]: e for e in d["entities"]}
    chunks = {c["id"]: c for c in d["chunks"]}
    ent_chunks = collections.defaultdict(set); chunk_ents = collections.defaultdict(set)
    for h in d["has_entity"]:
        ent_chunks[h["entity"]].add(h["chunk"]); chunk_ents[h["chunk"]].add(h["entity"])
    return d, ents, chunks, ent_chunks, chunk_ents
