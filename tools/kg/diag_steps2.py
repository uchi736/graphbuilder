# トレースで実際に文脈に入ったチャンクを起点に、STEP 2（起点実体）と STEP 3（1実体20経路の展開）を再現する
#   docker exec -i backend python - < diag_steps2.py > diag_steps2.json   （③グラフが載っている状態で）
import os, json, sys, re
from neo4j import GraphDatabase
from openai import OpenAI
TR = json.load(open("/tmp/trace_p3full.json", encoding="utf-8"))
TARGET = {"M06", "M12", "M04"}
norm = lambda s: " ".join(str(s).replace("�", "").split())
emb = OpenAI(base_url="http://host.docker.internal:8003/v1", api_key="dummy")
drv = GraphDatabase.driver(os.environ["NEO4J_URI"], auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"]))
out = {}
with drv.session(database="neo4j") as s:
    allc = s.run("MATCH (c:Chunk)-[:PART_OF]->(d:Document) RETURN c.id AS id, d.fileName AS doc, c.position AS pos, c.text AS text").data()
    for c in allc: c["n"] = norm(c["text"])
    for rec in TR:
        if rec["id"] not in TARGET: continue
        run = rec["runs"]["graph_vector_fulltext"]; q = rec["q"]
        qv = emb.embeddings.create(model="cl-nagoya/ruri-v3-310m", input=q).data[0].embedding
        r = {"q": q, "gold": rec["gold"], "answer": run["answer"]}
        # STEP1: 両索引の生スコア（正規化は figure 側で）
        r["vector_top5"] = s.run("""CALL db.index.vector.queryNodes('vector', 5, $qv) YIELD node, score
            MATCH (node)-[:PART_OF]->(d:Document) RETURN node.id AS id, d.fileName AS doc, node.position AS pos, round(score,4) AS score, left(node.text,60) AS head ORDER BY score DESC""", qv=qv).data()
        r["fulltext_top5"] = s.run("""CALL db.index.fulltext.queryNodes('keyword', $q, {limit:5}) YIELD node, score
            MATCH (node)-[:PART_OF]->(d:Document) RETURN node.id AS id, d.fileName AS doc, node.position AS pos, round(score,3) AS score, left(node.text,60) AS head ORDER BY score DESC""", q=q).data()
        # 実際に文脈に入ったチャンク（トレース）→ id を当てる
        kept = []
        for ch in run["chunks"]:
            p = norm(ch["text"])[:40]
            hit = next((c for c in allc if c["doc"] == ch["doc"] and p and p in c["n"]), None)
            kept.append({"doc": ch["doc"], "pos": hit["pos"] if hit else None, "id": hit["id"] if hit else None, "head": norm(ch["text"])[:80], "has_answer": ch.get("has_answer")})
        r["kept_chunks"] = kept
        ids = [k["id"] for k in kept if k["id"]]
        # STEP2: 起点実体（文書ごと、ヒットチャンク出現数順、40件）
        r["seed_entities"] = s.run("""MATCH (c:Chunk)-[:PART_OF]->(d:Document) WHERE c.id IN $ids
            MATCH (c)-[:HAS_ENTITY]->(e)
            WITH d.fileName AS doc, e, count(DISTINCT c) AS nChunks, collect(DISTINCT c.position) AS fromPos
            WITH doc, e, nChunks, fromPos,
                 size([(e)-[x]-(m) WHERE NOT type(x) IN ['HAS_ENTITY','PART_OF'] AND NOT m:Chunk AND NOT m:Document | x]) AS degree
            ORDER BY doc, nChunks DESC
            RETURN doc, [l IN labels(e) WHERE l<>'__Entity__'][0] AS label, e.id AS id, nChunks, fromPos, degree, e.embedding IS NOT NULL AS hasEmb""", ids=ids).data()
        # STEP3: 各起点の展開（実際の collect 句: 0-1ホップ LIMIT 20）
        exp = []
        for se in r["seed_entities"]:
            paths = s.run("""MATCH (e {id:$id}) WHERE e:__Entity__ WITH e LIMIT 1
                CALL { WITH e
                  OPTIONAL MATCH path=(e)(()-[rels:!HAS_ENTITY&!PART_OF]-()){0,1}(:!Chunk&!Document&!__Community__)
                  RETURN path LIMIT 20 }
                WITH path, [x IN relationships(path) | type(x)] AS ts, [n IN nodes(path) | n.id] AS ns,
                     [n IN nodes(path) | [l IN labels(n) WHERE l<>'__Entity__'][0]] AS ls,
                     [x IN relationships(path) | apoc.text.join([k IN keys(x) WHERE k<>'embedding' AND toString(x[k])<>'' | k+': '+toString(x[k])], ', ')] AS ps
                RETURN ts, ns, ls, ps""", id=se["id"]).data()
            included = []
            for p in paths:
                if p["ts"]:
                    oi = 1 if p["ns"][0] == se["id"] else 0
                    included.append({"type": p["ts"][0], "other": p["ns"][oi], "olabel": p["ls"][oi], "props": p["ps"][0]})
            allrels = s.run("""MATCH (e {id:$id})-[x]-(m) WHERE e:__Entity__ AND NOT type(x) IN ['HAS_ENTITY','PART_OF'] AND NOT m:Chunk AND NOT m:Document
                RETURN type(x) AS type, m.id AS other, [l IN labels(m) WHERE l<>'__Entity__'][0] AS olabel,
                       apoc.text.join([k IN keys(x) WHERE k<>'embedding' AND toString(x[k])<>'' | k+': '+toString(x[k])], ', ') AS props""", id=se["id"]).data()
            inc = {(i["type"], i["other"], i["props"]) for i in included}
            excluded = [a for a in allrels if (a["type"], a["other"], a["props"]) not in inc]
            exp.append({"id": se["id"], "label": se["label"], "doc": se["doc"], "nChunks": se["nChunks"], "degree": se["degree"],
                        "n_included": len(included), "included": included, "excluded": excluded})
        r["expansion"] = exp
        # 文脈に入った実体・関係の総数（トレース値）
        r["context_counts"] = {"chunks": len(run["chunks"]), "entities": len(run["entities"]), "relationships": len(run["relationships"])}
        out[rec["id"]] = r
json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
