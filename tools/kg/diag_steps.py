# 既定モードの各 STEP を手で再現して数値を取る（backend コンテナ内で実行、③グラフが載っている状態で）
#   docker exec -i backend python - < diag_steps.py > diag_steps.json
import os, json, sys
from neo4j import GraphDatabase
from openai import OpenAI
Q = {
 "M06": "発行目録に載っている RU・RH型初号機の基準では、性能計測はどの試験方案（図番）に基づきますか？",
 "M12": "TPL.TPS形初号機の性能計測時、外部給油方式の過給機の潤滑油温度は何℃以下であることが求められますか？",
}
emb = OpenAI(base_url="http://host.docker.internal:8003/v1", api_key="dummy")
drv = GraphDatabase.driver(os.environ["NEO4J_URI"], auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"]))
out = {}
with drv.session(database="neo4j") as s:
    idx = s.run("SHOW INDEXES YIELD name, type, labelsOrTypes, properties RETURN name, type, labelsOrTypes, properties").data()
    out["indexes"] = idx
    for qid, q in Q.items():
        qv = emb.embeddings.create(model="cl-nagoya/ruri-v3-310m", input=q).data[0].embedding
        r = {"q": q}
        # STEP 1a ベクトル上位5
        r["vector_top5"] = s.run("""CALL db.index.vector.queryNodes('vector', 5, $qv) YIELD node, score
            MATCH (node)-[:PART_OF]->(d:Document)
            RETURN node.id AS id, d.fileName AS doc, node.position AS pos, round(score,3) AS score, left(node.text,70) AS head ORDER BY score DESC""", qv=qv).data()
        # STEP 1b 全文索引（langchain hybrid は max で正規化して統合）
        ft = s.run("""CALL db.index.fulltext.queryNodes('keyword', $q) YIELD node, score
            MATCH (node)-[:PART_OF]->(d:Document)
            RETURN node.id AS id, d.fileName AS doc, node.position AS pos, round(score,3) AS score, left(node.text,70) AS head ORDER BY score DESC LIMIT 8""", q=q).data()
        mx = max([x["score"] for x in ft] or [1])
        for x in ft: x["norm"] = round(x["score"] / mx, 3)
        r["fulltext_top"] = ft
        # hybrid 統合（UNION → score 降順 → k=5 → 0.5 未満は捨てる）
        pool = {}
        for x in r["vector_top5"]: pool[x["id"]] = dict(x, src="vector", hyb=x["score"])
        for x in ft:
            if x["id"] in pool: pool[x["id"]]["src"] = "vector+fulltext"; pool[x["id"]]["hyb"] = max(pool[x["id"]]["hyb"], x["norm"])
            else: pool[x["id"]] = dict(x, src="fulltext", hyb=x["norm"])
        hyb = sorted(pool.values(), key=lambda x: -x["hyb"])[:5]
        r["hybrid_top5"] = hyb
        r["kept_chunks"] = [x for x in hyb if x["hyb"] >= 0.5]
        ids = [x["id"] for x in r["kept_chunks"]]
        # STEP 2 起点実体（文書ごと、ヒットチャンク出現数の多い順、40件）
        r["seed_entities"] = s.run("""MATCH (c:Chunk)-[:PART_OF]->(d:Document) WHERE c.id IN $ids
            MATCH (c)-[:HAS_ENTITY]->(e)
            WITH d.fileName AS doc, e, count(DISTINCT c) AS nChunks
            WITH doc, e, nChunks,
                 size([(e)-[x]-(m) WHERE NOT type(x) IN ['HAS_ENTITY','PART_OF'] AND NOT m:Chunk AND NOT m:Document | x]) AS degree,
                 e.embedding IS NOT NULL AS hasEmb
            ORDER BY doc, nChunks DESC
            RETURN doc, [l IN labels(e) WHERE l<>'__Entity__'][0] AS label, e.id AS id, nChunks, degree, hasEmb""", ids=ids).data()
        # STEP 3 各起点の展開（実際の collect 句をそのまま実行: 0-1 ホップ, LIMIT 20）
        exp = []
        for se in r["seed_entities"]:
            paths = s.run("""MATCH (e {id:$id}) WHERE e:__Entity__
                WITH e LIMIT 1
                CALL { WITH e
                  OPTIONAL MATCH path=(e)(()-[rels:!HAS_ENTITY&!PART_OF]-()){0,1}(:!Chunk&!Document&!__Community__)
                  RETURN path LIMIT 20 }
                WITH path, [r IN relationships(path) | type(r)] AS ts, [n IN nodes(path) | n.id] AS ns,
                     [r IN relationships(path) | apoc.text.join([k IN keys(r) WHERE k<>'embedding' AND toString(r[k])<>'' | k+': '+toString(r[k])], ', ')] AS ps
                RETURN ts, ns, ps""", id=se["id"]).data()
            included = []
            for p in paths:
                if p["ts"]:
                    included.append({"type": p["ts"][0], "other": p["ns"][1] if p["ns"][1] != se["id"] else p["ns"][0], "props": p["ps"][0]})
            allrels = s.run("""MATCH (e {id:$id})-[x]-(m) WHERE e:__Entity__ AND NOT type(x) IN ['HAS_ENTITY','PART_OF'] AND NOT m:Chunk AND NOT m:Document
                RETURN type(x) AS type, m.id AS other, apoc.text.join([k IN keys(x) WHERE k<>'embedding' AND toString(x[k])<>'' | k+': '+toString(x[k])], ', ') AS props""", id=se["id"]).data()
            inc_keys = {(i["type"], i["other"], i["props"]) for i in included}
            excluded = [a for a in allrels if (a["type"], a["other"], a["props"]) not in inc_keys]
            exp.append({"id": se["id"], "label": se["label"], "doc": se["doc"], "degree": se["degree"], "n_included": len(included), "included": included, "excluded": excluded})
        r["expansion"] = exp
        out[qid] = r
json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
