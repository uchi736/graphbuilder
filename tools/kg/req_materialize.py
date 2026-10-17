# 抽出 JSON (/tmp/req_extract.json) を Neo4j に書く。backend コンテナ内で実行。
#   docker exec -i backend python - <mode> < req_materialize.py    mode = clean | p4 | p3
#   clean : Document/Chunk 以外の __Entity__ 層を全削除（TS3-5 のものは残す）
#   p4    : ④ 規定ノード（Requirement）＋スロット述語＋Standard 間 REFERENCES ＋ ②への射影(JUDGED_BY 等の直線)
#   p3    : ③ 直線に限定子（when/kind/op/value/unit/source をリレーションのプロパティに）＋ REFERENCES
import os, sys, json, re, unicodedata
from neo4j import GraphDatabase
mode = sys.argv[1]
drv = GraphDatabase.driver(os.environ["NEO4J_URI"], auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"]))

ALIAS = {  # 名寄せ辞書（安定した語だけ）
  "RU形": "RU形過給機", "RU": "RU形過給機", "RH形": "RH形過給機", "RH": "RH形過給機", "RH3形": "RH3形過給機", "RH3": "RH3形過給機",
  "AT形": "AT形過給機", "AT": "AT形過給機", "TPL形": "TPL形過給機", "TPL": "TPL形過給機", "TPS形": "TPS形過給機", "TPS": "TPS形過給機",
  "VTR4形": "VTR4形過給機", "VTR4": "VTR4形過給機", "VTR0.1形": "VTR0.1形過給機", "VTR0.1": "VTR0.1形過給機",
  "吸込温度": "空気吸込温度", "吸入空気温度": "空気吸込温度", "空気吸入温度": "空気吸込温度", "風量": "空気量", "空気流量": "空気量",
  "回転速度": "回転数", "規定チェック回転速度": "回転数", "チェック回転数": "回転数", "Nt": "回転数",
  "管理曲線": "コンプレッサ性能管理曲線", "管理カーブ": "コンプレッサ性能管理曲線", "性能管理曲線": "コンプレッサ性能管理曲線",
}
def norm(s):
    if not s: return None
    s = unicodedata.normalize("NFKC", str(s)); s = re.sub(r"[（(](最新版|旧版|旧|改|案|参考)[)）]", "", s); s = re.sub(r"\s+", "", s).strip()
    return ALIAS.get(s, s)
def stdkey(s):  # 規格番号の正準形: HS- を落とし大文字化、改訂記号・空白除去
    s = unicodedata.normalize("NFKC", str(s)).upper().replace(" ", "")
    m = re.search(r"(?:HS-)?(VTR-T\d{3}|RU-\d{3}|TP-\d{3}|VTR-P\d{3})", s)
    if m: return m.group(1)
    m = re.search(r"(QC-KAI-\d{3}|V\d{5}|JIS[ A-Z]*\d{4})", s)
    return m.group(1) if m else None

with drv.session(database="neo4j") as s:
    if mode == "clean":
        n = s.run("MATCH (e:__Entity__) WHERE NOT (e)<-[:HAS_ENTITY]-(:Chunk)-[:PART_OF]->(:Document{fileName:'TS3-5.pdf'}) DETACH DELETE e RETURN count(*) AS c").single()["c"]
        s.run("MATCH (a:Document)-[r:REFERENCES]->() DELETE r")
        print(f"clean: deleted {n} entities"); sys.exit()

    recs = json.load(open("/tmp/req_extract.json", encoding="utf-8"))
    n_req = n_edge = n_ref = 0
    # 文書 = Standard 実体（規格番号）。チャンクから HAS_ENTITY で結ぶ
    for r in recs:
        s.run("MERGE (st:__Entity__ {id:$k}) ON CREATE SET st.description=$d SET st:Standard "
              "WITH st MATCH (c:Chunk {id:$cid}) MERGE (c)-[:HAS_ENTITY]->(st)", k=r["dockey"], d=f"規格 {r['file']}", cid=r["cid"])
    for r in recs:
        for i, q in enumerate(r["requirements"]):
            item = norm(q["item"]); conds = [norm(x) for x in q["condition"] if norm(x)]
            loc, ins, pts, actor, src = norm(q["location"]), norm(q["instrument"]), norm(q["points"]), norm(q["actor"]), norm(q["source"])
            cr = q["criterion"] or {}
            ctext = cr.get("text") or ""; op, val, unit, kind = cr.get("op") or "", cr.get("value") or "", cr.get("unit") or "", cr.get("kind") or ""
            crit_id = norm(ctext) or (f"{op}{val}{unit}" if val else None)
            cond_txt = "・".join(conds) if conds else ""
            desc = f"{item}" + (f" {op} {val}{unit}" if val else (f" {ctext}" if ctext else "")) + (f"（{cond_txt}）" if cond_txt else "") + (f" @{loc}" if loc else "") + (f" by {ins}" if ins else "") + (f" {pts}" if pts else "")
            rid = f"REQ-{r['dockey']}-{src or 'p'+str(r['pos'])}-{i+1}"
            if mode == "p4":
                s.run("""MERGE (req:__Entity__ {id:$rid}) SET req:Requirement SET req.description=$desc, req.kind=$kind, req.op=$op, req.value=$val, req.unit=$unit, req.source=$src, req.points=$pts
                         WITH req MATCH (c:Chunk {id:$cid}) MERGE (c)-[:HAS_ENTITY]->(req)
                         WITH req MATCH (st:Standard {id:$dk}) MERGE (st)-[:HAS_REQUIREMENT]->(req) MERGE (req)-[:SOURCE]->(st)
                         WITH req MERGE (it:__Entity__ {id:$item}) SET it:MeasurementItem MERGE (req)-[:ON]->(it)
                         WITH req, it MATCH (c:Chunk {id:$cid}) MERGE (c)-[:HAS_ENTITY]->(it)""",
                      rid=rid, desc=desc, kind=kind, op=op, val=val, unit=unit, src=src, pts=pts, cid=r["cid"], dk=r["dockey"], item=item)
                for cnd in conds:
                    s.run("MATCH (req:Requirement {id:$rid}) MERGE (cd:__Entity__ {id:$c}) SET cd:Condition MERGE (req)-[:APPLIES_WHEN]->(cd) WITH cd MATCH (ch:Chunk {id:$cid}) MERGE (ch)-[:HAS_ENTITY]->(cd)", rid=rid, c=cnd, cid=r["cid"])
                if loc: s.run("MATCH (req:Requirement {id:$rid}) MERGE (l:__Entity__ {id:$l}) SET l:Location MERGE (req)-[:MEASURED_AT]->(l) WITH l MATCH (ch:Chunk {id:$cid}) MERGE (ch)-[:HAS_ENTITY]->(l)", rid=rid, l=loc, cid=r["cid"])
                if ins: s.run("MATCH (req:Requirement {id:$rid}) MERGE (n:__Entity__ {id:$n}) SET n:Instrument MERGE (req)-[:MEASURED_BY]->(n) WITH n MATCH (ch:Chunk {id:$cid}) MERGE (ch)-[:HAS_ENTITY]->(n)", rid=rid, n=ins, cid=r["cid"])
                if actor: s.run("MATCH (req:Requirement {id:$rid}) MERGE (a:__Entity__ {id:$a}) SET a:Organization MERGE (req)-[:PERFORMED_BY]->(a) WITH a MATCH (ch:Chunk {id:$cid}) MERGE (ch)-[:HAS_ENTITY]->(a)", rid=rid, a=actor, cid=r["cid"])
                if crit_id:
                    s.run("MATCH (req:Requirement {id:$rid}) MERGE (cr:__Entity__ {id:$c}) SET cr:Criterion SET cr.op=$op, cr.value=$val, cr.unit=$unit MERGE (req)-[:HAS_CRITERION]->(cr) WITH cr MATCH (ch:Chunk {id:$cid}) MERGE (ch)-[:HAS_ENTITY]->(cr)", rid=rid, c=crit_id, op=op, val=val, unit=unit, cid=r["cid"])
                # ②への射影（正解データ採点と既存ツール向けの直線）
                if crit_id: s.run("MATCH (it:MeasurementItem {id:$item}), (cr:Criterion {id:$c}) MERGE (it)-[:JUDGED_BY]->(cr)", item=item, c=crit_id)
                if loc: s.run("MATCH (it:MeasurementItem {id:$item}), (l:Location {id:$l}) MERGE (it)-[:MEASURED_AT]->(l)", item=item, l=loc)
                if ins: s.run("MATCH (it:MeasurementItem {id:$item}), (n:Instrument {id:$n}) MERGE (it)-[:MEASURED_BY]->(n)", item=item, n=ins)
                for cnd in conds: s.run("MATCH (it:MeasurementItem {id:$item}), (cd:Condition {id:$c}) MERGE (it)-[:APPLIES_TO]->(cd)", item=item, c=cnd)
                n_req += 1
            elif mode == "p3":
                when = cond_txt or None
                s.run("MERGE (it:__Entity__ {id:$item}) SET it:MeasurementItem WITH it MATCH (c:Chunk {id:$cid}) MERGE (c)-[:HAS_ENTITY]->(it) WITH it MATCH (st:Standard {id:$dk}) MERGE (it)-[:DEFINED_IN {section:coalesce($src,'')}]->(st)", item=item, cid=r["cid"], dk=r["dockey"], src=src)
                if crit_id:
                    s.run("""MATCH (it:MeasurementItem {id:$item}) MERGE (cr:__Entity__ {id:$c}) SET cr:Criterion SET cr.description=$ctext
                             MERGE (it)-[e:JUDGED_BY {when:coalesce($when,''), source:coalesce($src,'')}]->(cr) SET e.kind=$kind, e.op=$op, e.value=$val, e.unit=$unit
                             WITH cr MATCH (ch:Chunk {id:$cid}) MERGE (ch)-[:HAS_ENTITY]->(cr)""", item=item, c=crit_id, ctext=ctext, when=when, src=src, kind=kind, op=op, val=val, unit=unit, cid=r["cid"]); n_edge += 1
                if loc: s.run("MATCH (it:MeasurementItem {id:$item}) MERGE (l:__Entity__ {id:$l}) SET l:Location MERGE (it)-[e:MEASURED_AT {when:coalesce($when,''), points:coalesce($pts,''), source:coalesce($src,'')}]->(l) WITH l MATCH (ch:Chunk {id:$cid}) MERGE (ch)-[:HAS_ENTITY]->(l)", item=item, l=loc, when=when, pts=pts, src=src, cid=r["cid"]); n_edge += 1
                if ins: s.run("MATCH (it:MeasurementItem {id:$item}) MERGE (n:__Entity__ {id:$n}) SET n:Instrument MERGE (it)-[e:MEASURED_BY {when:coalesce($when,''), source:coalesce($src,'')}]->(n) WITH n MATCH (ch:Chunk {id:$cid}) MERGE (ch)-[:HAS_ENTITY]->(n)", item=item, n=ins, when=when, src=src, cid=r["cid"]); n_edge += 1
                for cnd in conds:
                    s.run("MATCH (it:MeasurementItem {id:$item}) MERGE (cd:__Entity__ {id:$c}) SET cd:Condition MERGE (it)-[:APPLIES_TO {source:coalesce($src,'')}]->(cd) WITH cd MATCH (ch:Chunk {id:$cid}) MERGE (ch)-[:HAS_ENTITY]->(cd)", item=item, c=cnd, src=src, cid=r["cid"])
                if actor: s.run("MATCH (it:MeasurementItem {id:$item}) MERGE (a:__Entity__ {id:$a}) SET a:Organization MERGE (it)-[:PERFORMED_BY {source:coalesce($src,'')}]->(a)", item=item, a=actor, src=src)
                n_req += 1
        # 参照: Standard → Standard（実体空間。標準の展開で辿れる）
        for ref in r["references"]:
            k = stdkey(ref["to"])
            if not k or k == r["dockey"]: continue
            s.run("MATCH (a:Standard {id:$a}) MERGE (b:__Entity__ {id:$b}) SET b:Standard MERGE (a)-[x:REFERENCES]->(b) SET x.section=coalesce($sec,''), x.context=coalesce($ctx,'') "
                  "WITH b MATCH (c:Chunk {id:$cid}) MERGE (c)-[:HAS_ENTITY]->(b)", a=r["dockey"], b=k, sec=ref["section"], ctx=(ref["context"] or "")[:120], cid=r["cid"]); n_ref += 1
    tot = s.run("MATCH (e:__Entity__) RETURN count(e) AS c").single()["c"]
    print(f"{mode}: requirements={n_req} edges(p3)={n_edge} references={n_ref} total_entities={tot}")
