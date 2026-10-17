# 質問→検索→展開→文脈→回答 のトレースを取る。ホストで実行: python3 trace_qa.py <tag> <mode> [<mode>...]
import json, sys, urllib.request, urllib.parse, subprocess, time
S="/tmp/claude-1000/-home-uchida/4fdbd8d5-c462-43e6-88f0-fb9cf37aba38/scratchpad/std"; B="http://localhost:8440"
tag=sys.argv[1]; modes=sys.argv[2:]
CRED={"uri":"neo4j://database:7687","userName":"neo4j","password":"kgb-please-change","database":"neo4j","email":"test@example.com"}
def post(path, **kw):
    data=urllib.parse.urlencode({**CRED, **kw}).encode()
    return json.load(urllib.request.urlopen(urllib.request.Request(B+path, data=data), timeout=300))
def cypher(q):
    out=subprocess.run(["docker","exec","neo4j-kgb","cypher-shell","-u","neo4j","-p","kgb-please-change","--format","plain",q],capture_output=True,text=True).stdout
    return out
qs=json.load(open(f"{S}/eval_multihop.json",encoding="utf-8"))
traces=[]
for q in qs:
    rec={"id":q["id"],"q":q["q"],"gold":q["gold"],"hops":q["hops"],"docs":q["docs"],"runs":{}}
    for m in modes:
        t0=time.time()
        d=post("/chat_bot", model="gemma4", mode=m, document_names="[]", session_id=f"trace-{tag}-{m}-{q['id']}", embedding_provider="vllm", embedding_model="cl-nagoya/ruri-v3-310m", question=q["q"])
        x=d.get("data") or {}; info=x.get("info") or {}
        ctx=((info.get("metric_details") or {}).get("contexts") or "")
        run={"answer":(x.get("message") or "").strip(),"sources":info.get("sources"),"mode":info.get("mode"),"sec":round(time.time()-t0,1),"chunks":[],"entities":[],"relationships":[],"context_chars":len(ctx),"context":ctx}
        try:
            g=post("/chunk_entities", nodedetails=json.dumps(info.get("nodedetails") or {}), entities=json.dumps(info.get("entities") or []), mode=m)
            gd=g.get("data") or {}
            run["chunks"]=[{"doc":c.get("fileName") or (c.get("chunk") or {}).get("fileName"),"text":((c.get("text") or (c.get("chunk") or {}).get("text") or "")[:400])} for c in (gd.get("chunk_data") or [])]
            run["entities"]=[{"label":[l for l in (n.get("labels") or []) if l!="__Entity__"][:1],"id":str((n.get("properties") or {}).get("id",""))[:60],"desc":str((n.get("properties") or {}).get("description",""))[:100]} for n in (gd.get("nodes") or [])]
            rels=gd.get("relationships") or []
            ids=[r.get("element_id") for r in rels if r.get("element_id")]
            props={}
            if ids:
                idlist=",".join(f"'{i}'" for i in ids)
                out=cypher(f"MATCH ()-[r]->() WHERE elementId(r) IN [{idlist}] RETURN elementId(r)+'|'+startNode(r).id+'|'+type(r)+'|'+endNode(r).id+'|'+apoc.text.join([k IN keys(r) WHERE k<>'embedding' AND toString(r[k])<>'' | k+': '+toString(r[k])], ', ')")
                for line in out.splitlines()[1:]:
                    parts=line.strip().strip('"').split("|")
                    if len(parts)>=4: props[parts[0]]={"s":parts[1],"t":parts[2],"o":parts[3],"props":parts[4] if len(parts)>4 else ""}
            run["relationships"]=[props.get(r.get("element_id"),{"s":"?","t":r.get("type"),"o":"?","props":""}) for r in rels]
        except Exception as e:
            run["trace_error"]=str(e)[:120]
        rec["runs"][m]=run
        print(f"  {q['id']} {m:22s} {run['sec']:>5}s src={run['sources']} ents={len(run['entities'])} rels={len(run['relationships'])} | {run['answer'][:60].replace(chr(10),' ')}", flush=True)
    traces.append(rec)
    json.dump(traces, open(f"{S}/trace_{tag}.json","w",encoding="utf-8"), ensure_ascii=False, indent=1)
print("saved", f"{S}/trace_{tag}.json")
