# ナレッジグラフ後処理: 様式ノイズ除去 → 正規化名寄せ → REFERENCES 付与
# backend コンテナ内で実行 (docker exec -i backend python - < this)
import os, re, unicodedata, json
from neo4j import GraphDatabase

FORM_NOISE = {"審査","承認","作成","改訂","改正","来歴","審査結果","控","原","合計","担当","処置区分",
              "発行","配布","配布先","番号","日付","年月日","頁","別紙","様式","印","確認欄",
              "改正番号","改正回数","この基準","本基準","協議・通知票","一部見直し","全面見直し","協議部門名","備考","制定","新規制定"}
FORM_PATTERNS = [r"^[0-9A-Z０-９Ａ-Ｚ]改正$", r"^\d{2,4}[./]\d{1,2}[./]\d{1,2}$", r"^[ＡＢＣＤＥ]$"]  # A改正 / 02.06.12 / 改訂記号
KEEP_SHORT = {"RU","RH","AT","JG","UT","RT","Lo","Ａi","℃"}  # 短くても意味がある語(型式・検査法)

def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)            # 全角→半角・互換分解
    s = re.sub(r"[（(](最新版|旧版|旧|改|案|参考|新規制定|制定)[)）]", "", s)  # 意味の無い付記だけ除去
    s = s.replace(" ", "").replace("\u3000", "")
    return s.upper()

DOCKEY_PAT = re.compile(r"((?:HS-)?(?:VTR-T|RU-|TP-|VTR-P|TPS-)\d+|V\d{5,}|QC-[A-Z]+-\d+)")
def canon(k: str) -> str:
    return re.sub(r"^HS-", "", k)

drv = GraphDatabase.driver(os.environ["NEO4J_URI"], auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"]))
rep = {}
with drv.session(database=os.environ.get("NEO4J_DATABASE","neo4j")) as s:
    # 1) 様式ノイズ・記号/純数字 ID の除去
    ids = [r["id"] for r in s.run("MATCH (e:__Entity__) RETURN e.id AS id")]
    kill = []
    for i in ids:
        n = norm(i)
        if i in FORM_NOISE or n in {norm(x) for x in FORM_NOISE}: kill.append(i); continue
        if any(re.fullmatch(pt, unicodedata.normalize("NFKC", i)) for pt in FORM_PATTERNS): kill.append(i); continue
        if i in KEEP_SHORT or n in {norm(x) for x in KEEP_SHORT}: continue
        if re.fullmatch(r"[\W_]+|\d+(\.\d+)?|[A-Za-zＡ-Ｚａ-ｚ]", unicodedata.normalize("NFKC", i) or " "):
            kill.append(i)  # 記号のみ / 純数字 / 英1文字
    r = s.run("UNWIND $ids AS x MATCH (e:__Entity__ {id:x}) DETACH DELETE e RETURN count(*) AS c", ids=kill).single()
    rep["noise_deleted"] = len(kill); rep["noise_examples"] = kill[:15]

    # 2) 正規化キーで同一になるエンティティを APOC でマージ
    rows = s.run("MATCH (e:__Entity__) RETURN elementId(e) AS eid, e.id AS id").data()
    groups = {}
    for r0 in rows: groups.setdefault(norm(r0["id"]), []).append(r0)
    merged = 0; merge_examples = []
    for k, g in groups.items():
        if len(g) < 2 or not k: continue
        g.sort(key=lambda x: len(x["id"]))  # 最短の表記を代表に
        eids = [x["eid"] for x in g]
        s.run("MATCH (e) WHERE elementId(e) IN $eids WITH collect(e) AS ns CALL apoc.refactor.mergeNodes(ns, {properties:'discard', mergeRels:true}) YIELD node RETURN node", eids=eids)
        merged += len(g)-1
        if len(merge_examples) < 10: merge_examples.append([x["id"] for x in g])
    rep["merged_nodes"] = merged; rep["merge_examples"] = merge_examples

    # 3) Document に docKey を付与し、規格番号エンティティと突合して REFERENCES
    docs = s.run("MATCH (d:Document) WHERE d.fileName IS NOT NULL RETURN d.fileName AS f").data()
    keymap = {}
    for d0 in docs:
        m = DOCKEY_PAT.search(norm(d0["f"]))
        if m: keymap[d0["f"]] = canon(m.group(1))
    for f, k in keymap.items():
        s.run("MATCH (d:Document {fileName:$f}) SET d.docKey = $k", f=f, k=k)
    rep["docKeys"] = keymap

    ents = s.run("MATCH (e:__Entity__)<-[:HAS_ENTITY]-(:Chunk)-[:PART_OF]->(src:Document) WHERE src.fileName IS NOT NULL RETURN DISTINCT e.id AS id, src.fileName AS src").data()
    refs = set(); dangling = set()
    for r0 in ents:
        m = DOCKEY_PAT.search(norm(r0["id"]))
        if not m: continue
        key = canon(m.group(1))
        tgt = next((f for f, k in keymap.items() if k == key), None)
        if tgt and tgt != r0["src"]: refs.add((r0["src"], tgt, key))
        elif not tgt: dangling.add(key)
    for src, tgt, key in refs:
        s.run("MATCH (a:Document {fileName:$s}), (b:Document {fileName:$t}) MERGE (a)-[r:REFERENCES]->(b) SET r.via = $k", s=src, t=tgt, k=key)
    rep["references"] = sorted([f"{a} -> {b} (via {k})" for a, b, k in refs])
    rep["dangling_refs"] = sorted(dangling)

print(json.dumps(rep, ensure_ascii=False, indent=1))
