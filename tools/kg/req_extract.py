# 規定レコード抽出: Neo4j の Chunk 本文 → gemma4 → JSON（規定 / 参照）。backend コンテナ内で実行。
#   docker exec -i backend python - [max_chunks] < req_extract.py   → /tmp/req_extract.json に保存
import os, sys, json, re, time, unicodedata
from neo4j import GraphDatabase
from openai import OpenAI

MAX = int(sys.argv[1]) if len(sys.argv) > 1 else 10**6
OUT = "/tmp/req_extract.json"
client = OpenAI(base_url="http://host.docker.internal:8000/v1", api_key="dummy")
MODEL = "google/gemma-4-26B-A4B-it"

DOCKEY = {"HS-VTR-T001E.pdf":"VTR-T001","HS-VTR-T002F.pdf":"VTR-T002","VTR-T003D.pdf":"VTR-T003",
          "VTR-T004C.pdf":"VTR-T004","VTR-T005B.pdf":"VTR-T005","VTR-T_発行目録.pdf":"VTR-T-INDEX"}

PROMPT = """あなたは産業規格文書から「規定」を構造化する抽出器です。以下の本文（過給機の試運転・検査基準の一部、文書番号 {dockey}）から、
規定（何を・どの条件で・どう測り・何で合否を決めるか）と、他文書への参照を JSON で抜き出してください。

規則:
- requirements: 本文に明示された規定のみ。推測しない。条件が違えば別レコード（例: VTR4形とTPL.TPS形で点数が違うなら2レコード）。
- item: 対象の計測項目/試験項目/対象物（例: 空気吸込温度, 外部振動, 管理曲線）。短い名詞で。
- condition: 適用条件（型式・給油方式・状態・台数など）。無ければ null。複数は配列。
- location / instrument / points: 計測箇所・計測機器・計測点数。無ければ null。
- criterion: 判定や設定値。kind(相互差/上限/下限/範囲/期限/頻度/責任 など), op(≤,≥,=,±,範囲,以内 など), value(数値や式。例 2, "Nmax×0.8", "入口温度+45"), unit(℃,kPa,%,点,台,年 など), text(原文のまま)。無ければ null。
- actor: 実施・承認する部門/役職があれば。無ければ null。
- source: 本文中の項番（例 "2-1(1)b", "3-6"）。無ければ null。
- references: 本文で参照している他の規格番号・図番（例 HS-VTR-T001, QC-KAI-027, V02313, JIS B 8340）。to は原文表記、section は参照している項番があれば。
- JSON 以外は出力しない。該当が無ければ空配列。

出力形式:
{{"requirements":[{{"item":"", "condition":null, "location":null, "instrument":null, "points":null,
  "criterion":{{"kind":"", "op":"", "value":"", "unit":"", "text":""}} , "actor":null, "source":null}}],
 "references":[{{"to":"", "section":null, "context":""}}]}}

本文:
{text}
"""

MAX_TOKENS = int(os.environ.get("REQ_EXTRACT_MAX_TOKENS", "6000"))


def parse_json(raw):
    """LLM の出力から JSON を読む。出力上限で途中で切れた場合は、完結しているレコードだけを拾う。"""
    m = re.search(r"\{.*\}", raw, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    out = {"requirements": [], "references": [], "_salvaged": True}
    dec = json.JSONDecoder()
    for key in ("requirements", "references"):
        k = raw.find('"%s"' % key)
        i = raw.find("[", k) if k >= 0 else -1
        if i < 0:
            continue
        i += 1
        while i < len(raw):
            while i < len(raw) and raw[i] in " \n\r\t,":
                i += 1
            if i >= len(raw) or raw[i] != "{":
                break
            try:
                obj, end = dec.raw_decode(raw, i)
            except json.JSONDecodeError:
                break
            out[key].append(obj)
            i = end
    return out


def norm(s):
    if s is None: return None
    s = unicodedata.normalize("NFKC", str(s)).strip()
    return re.sub(r"\s+", " ", s)

drv = GraphDatabase.driver(os.environ["NEO4J_URI"], auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"]))
with drv.session(database="neo4j") as s:
    chunks = s.run("MATCH (c:Chunk)-[:PART_OF]->(d:Document) WHERE d.fileName IN $names "
                   "RETURN c.id AS cid, d.fileName AS f, c.position AS pos, c.text AS text ORDER BY f, pos",
                   names=list(DOCKEY)).data()
chunks = chunks[:MAX]
done = {}
if os.path.exists(OUT):
    try: done = {r["cid"]: r for r in json.load(open(OUT, encoding="utf-8"))}
    except Exception: done = {}
results = []; t0 = time.time(); n_req = n_ref = 0
for i, c in enumerate(chunks, 1):
    if c["cid"] in done and not done[c["cid"]].get("error"): results.append(done[c["cid"]]); continue
    dockey = DOCKEY[c["f"]]
    rec = {"cid": c["cid"], "file": c["f"], "dockey": dockey, "pos": c["pos"], "requirements": [], "references": [], "error": None}
    try:
        r = client.chat.completions.create(model=MODEL, temperature=0, max_tokens=MAX_TOKENS,
              messages=[{"role": "user", "content": PROMPT.format(dockey=dockey, text=c["text"])}])
        raw = r.choices[0].message.content or ""
        rec["finish"] = r.choices[0].finish_reason
        obj = parse_json(raw)
        for q in obj.get("requirements", []) or []:
            if not q or not q.get("item"): continue
            cond = q.get("condition"); cond = [norm(x) for x in cond] if isinstance(cond, list) else ([norm(cond)] if cond else [])
            cr = q.get("criterion") or {}
            rec["requirements"].append({"item": norm(q.get("item")), "condition": [x for x in cond if x],
                "location": norm(q.get("location")), "instrument": norm(q.get("instrument")), "points": norm(q.get("points")),
                "criterion": {k: norm(cr.get(k)) for k in ("kind","op","value","unit","text")} if any(cr.get(k) for k in ("value","text")) else None,
                "actor": norm(q.get("actor")), "source": norm(q.get("source"))})
        for ref in obj.get("references", []) or []:
            if ref and ref.get("to"): rec["references"].append({"to": norm(ref["to"]), "section": norm(ref.get("section")), "context": norm(ref.get("context"))})
    except Exception as e:
        rec["error"] = str(e)[:200]
    results.append(rec); n_req += len(rec["requirements"]); n_ref += len(rec["references"])
    json.dump(results, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[{i}/{len(chunks)}] {dockey} pos={c['pos']} req={len(rec['requirements'])} ref={len(rec['references'])} {'ERR '+rec['error'] if rec['error'] else ''} ({time.time()-t0:.0f}s)", flush=True)
print(f"done: chunks={len(results)} requirements={sum(len(r['requirements']) for r in results)} references={sum(len(r['references']) for r in results)} → {OUT}")
