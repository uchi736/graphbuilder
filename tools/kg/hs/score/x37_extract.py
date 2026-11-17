# X37: 本番のグラフのチャンクの本文を EDC の /extract に渡し、返ったトリプルから採点用の書き出しを作る（Graph Builder の後処理は掛けない）。
# 使い方: python3 score/x37_extract.py <EDC の URL> <出力ディレクトリ> <文書,文書,…> [同時数]
import json, re, sys, os, time, urllib.request, concurrent.futures as cf
base, out, docs = sys.argv[1].rstrip("/"), sys.argv[2], sys.argv[3].split(",")
conc = int(sys.argv[4]) if len(sys.argv) > 4 else 2
os.makedirs(out, exist_ok=True)
D = json.load(open("runs/prod_full56_1113/graph_dump.json", encoding="utf-8"))
chunks = sorted([c for c in D["chunks"] if c["fileName"] in docs], key=lambda c: (c["fileName"], int(c.get("position") or 0)))
# backend/src/edc_extraction.py の _resolve_self_reference と同じ読み替え
_SELF = re.compile(r"(この基準|本基準|当基準|この規格|本規格|この要領|本要領)"); _HEAD = re.compile(r"^\[([^\]>]+?)\s*(?:>|\])")
_NUM = re.compile(r"^(?:HS[-\s]?)?([A-Z]{2,4})-([A-Z]?\d{3})([A-Z])?$")
def resolve(text):
    m = _HEAD.match(text or "")
    if not m: return text
    name = m.group(1).strip(); n = _NUM.match(name.replace(" ", ""))
    name = f"HS-{n.group(1)}-{n.group(2)}" if n else name
    if not name: return text
    head, sep, body = text.partition("\n")
    if not head.startswith("["): head, sep, body = "", "", text
    return head + sep + _SELF.sub(name, body)
def call(c):
    payload = {"text": resolve(c.get("text") or ""), "doctype": "購入品_品質要求基準", "chunk_method": "recursive", "enrich_schema": False}
    t0 = time.time()
    for attempt in range(2):
        try:
            req = urllib.request.Request(base + "/extract", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
            body = json.loads(urllib.request.urlopen(req, timeout=900).read())
            return c["id"], body, time.time() - t0, None
        except Exception as e:
            err = str(e)
    return c["id"], {}, time.time() - t0, err
res = {}
t0 = time.time()
with cf.ThreadPoolExecutor(conc) as ex:
    for i, (cid, body, sec, err) in enumerate(ex.map(call, chunks)):
        res[cid] = {"sec": round(sec, 1), "err": err, "n_truncated": body.get("n_truncated", 0), "n_llm_calls": body.get("n_llm_calls"),
                    "triplets": body.get("triplets") or [], "dropped": body.get("dropped") or [], "doctype": body.get("doctype")}
        if i % 10 == 0: print(i, len(chunks), round(time.time() - t0), flush=True)
json.dump(res, open(os.path.join(out, "raw.json"), "w", encoding="utf-8"), ensure_ascii=False)
ents = {}; rels = []; has = []; seen = set()
for c in chunks:
    for t in res[c["id"]]["triplets"]:
        s, o, r = str(t.get("subject") or "").strip(), str(t.get("object") or "").strip(), re.sub(r"\s+", "_", str(t.get("relation") or "").strip())
        if not (s and o and r): continue
        ids = []
        for name, ty in ((s, t.get("subject_type") or "Entity"), (o, t.get("object_type") or "Entity")):
            eid = f"{ty}:{name}"
            ents.setdefault(eid, {"id": eid, "labels": ["__Entity__", ty], "props": {"id": name}}); ids.append(eid)
            if (c["id"], eid) not in seen: seen.add((c["id"], eid)); has.append({"chunk": c["id"], "entity": eid})
        k = (ids[0], r, ids[1])
        if k not in seen: seen.add(k); rels.append({"id": f"r{len(rels)}", "source": ids[0], "target": ids[1], "type": r, "props": {"source_chunk_id": c["id"]}})
json.dump({"chunks": chunks, "refers_to": [], "has_entity": has, "entities": list(ents.values()), "relationships": rels},
          open(os.path.join(out, "graph_dump.json"), "w", encoding="utf-8"), ensure_ascii=False)
summ = {"chunks": len(chunks), "entities": len(ents), "relationships": len(rels), "sec_total": round(time.time() - t0), "sec_sum": round(sum(v["sec"] for v in res.values())),
        "truncated": sum(v["n_truncated"] or 0 for v in res.values()), "errors": sum(1 for v in res.values() if v["err"]), "dropped": sum(len(v["dropped"]) for v in res.values()),
        "doctypes": sorted({str(v["doctype"]) for v in res.values()})}
json.dump(summ, open(os.path.join(out, "summary.json"), "w", encoding="utf-8"), ensure_ascii=False); print(summ)
