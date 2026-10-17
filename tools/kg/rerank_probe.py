# 16問: 候補(ベクトル上位20 ∪ 全文上位20)を今の統合順(上位4) と ruri リランカー順(上位4) で比べ、答えの語を含むチャンクが入るかを見る
import os, json, re, unicodedata, requests
from neo4j import GraphDatabase
from openai import OpenAI
QS = json.load(open("/tmp/eval_multihop.json", encoding="utf-8"))
KEYS={"M01":["50kPa","50 kPa"],"M02":["10分"],"M03":["0.8"],"M04":["±3%","±3％","3%以内","3％以内"],"M05":["02313"],"M06":["00862"],"M07":["-10%","−10%","－10%","0~-10","0〜-10","0～-10","0~−10"],"M08":["6点","02313"],"M09":["2点","4台"],"M10":["検査部門長"],"M11":["027","00862","02313"],"M12":["+45","＋45"],"M13":["回転機事業部","過給機部検査課"],"M14":["取付ない","取り付けない","取付けない","6点"],"M15":["±2%","±2％","2%以内","2％以内","C側"],"M16":["T側"]}
emb = OpenAI(base_url="http://host.docker.internal:8003/v1", api_key="dummy")
drv = GraphDatabase.driver(os.environ["NEO4J_URI"], auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"]))
norm = lambda s: unicodedata.normalize("NFKC", " ".join(str(s).split()))
# langchain の remove_lucene_chars と同じ: Lucene の特殊文字を空白にする
def lucene(q):
    for ch in ["+","-","&","|","!","(",")","{","}","[","]","^","\"","~","*","?",":","\\","/"]:
        q = q.replace(ch, " ")
    return " ".join(q.split())
def rerank(q, docs):
    r = requests.post("http://host.docker.internal:8006/v1/rerank", json={"model":"cl-nagoya/ruri-v3-reranker-310m","query":q,"documents":[d[:1200] for d in docs]}, timeout=300)
    r.raise_for_status()
    sc = [None]*len(docs)
    for x in r.json()["results"]: sc[x["index"]] = x["relevance_score"]
    return sc
out=[]; agg={"hyb4":0,"rr4":0,"cand":0,"n":0}
with drv.session(database="neo4j") as s:
    for q in QS:
        qid, text = q["id"], q["q"]; keys = KEYS[qid]
        qv = emb.embeddings.create(model="cl-nagoya/ruri-v3-310m", input=text).data[0].embedding
        vec = s.run("CALL db.index.vector.queryNodes('vector', 20, $qv) YIELD node, score MATCH (node)-[:PART_OF]->(d:Document) RETURN node.id AS id, d.fileName AS doc, node.position AS pos, score, node.text AS text", qv=qv).data()
        ft = s.run("CALL db.index.fulltext.queryNodes('keyword', $q, {limit:20}) YIELD node, score MATCH (node)-[:PART_OF]->(d:Document) RETURN node.id AS id, d.fileName AS doc, node.position AS pos, score, node.text AS text", q=lucene(text)).data()
        vmax = max([x["score"] for x in vec] or [1]); fmax = max([x["score"] for x in ft] or [1])
        cand = {}
        for x in vec: cand[x["id"]] = dict(x, hyb=x["score"]/vmax, src="v")
        for x in ft:
            n = x["score"]/fmax
            if x["id"] in cand: cand[x["id"]]["hyb"] = max(cand[x["id"]]["hyb"], n); cand[x["id"]]["src"] += "f"
            else: cand[x["id"]] = dict(x, hyb=n, src="f")
        items = list(cand.values())
        for it in items: it["ans"] = [k for k in keys if k in norm(it["text"])]
        # 答えを含むチャンクが全チャンク中に存在するか（候補外も含めて）
        allans = s.run("MATCH (c:Chunk)-[:PART_OF]->(d:Document) RETURN c.id AS id, d.fileName AS doc, c.position AS pos, c.text AS text").data()
        exists = [(a["doc"], a["pos"]) for a in allans if any(k in norm(a["text"]) for k in keys)]
        hyb = sorted(items, key=lambda x: -x["hyb"])
        sc = rerank(text, [it["text"] for it in items])
        for it, v in zip(items, sc): it["rr"] = v
        rr = sorted(items, key=lambda x: -(x["rr"] or -1))
        def first_rank(lst): 
            for i, it in enumerate(lst, 1):
                if it["ans"]: return i, it
            return None, None
        hr, hit_h = first_rank(hyb); rrk, hit_r = first_rank(rr)
        rec = {"id": qid, "q": text[:40], "n_cand": len(items), "answer_chunks_total": len(exists), "in_cand": sum(1 for it in items if it["ans"]),
               "hyb_rank": hr, "rr_rank": rrk, "hyb_top4": [(x["doc"][:9], x["pos"], bool(x["ans"])) for x in hyb[:4]], "rr_top4": [(x["doc"][:9], x["pos"], round(x["rr"],3), bool(x["ans"])) for x in rr[:4]],
               "rr_gap": (round(rr[0]["rr"],3), round(rr[3]["rr"],3)) if len(rr) >= 4 else None}
        out.append(rec); agg["n"] += 1
        agg["cand"] += 1 if rec["in_cand"] else 0; agg["hyb4"] += 1 if (hr and hr <= 4) else 0; agg["rr4"] += 1 if (rrk and rrk <= 4) else 0
        print(f"{qid} 候補{len(items):2d} 答えチャンク:全体{len(exists)}件/候補内{rec['in_cand']}件 | 今の統合: 答えの初出 {hr} 位 | リランク後: {rrk} 位 | rr上位4 {rec['rr_top4']}", flush=True)
print("集計:", agg)
json.dump(out, open("/tmp/rerank_probe.json","w",encoding="utf-8"), ensure_ascii=False, indent=1)
