# トレースのチャンク(先頭140字)を Neo4j の全文と照合し、答えの語が本文にあるかを付与
import json,sys,subprocess
S="/tmp/claude-1000/-home-uchida/4fdbd8d5-c462-43e6-88f0-fb9cf37aba38/scratchpad/std"
KEYS={"M01":["50kPa","50 kPa"],"M02":["10分"],"M03":["0.8"],"M04":["±3%","±3％","3%以内","3％以内"],"M05":["02313"],"M06":["00862"],"M07":["-10%","−10%","－10%","0~-10","0〜-10","0～-10","0~−10"],"M08":["6点","02313"],"M09":["2点","4台"],"M10":["検査部門長"],"M11":["027","00862","02313"],"M12":["+45","＋45"],"M13":["回転機事業部","過給機部検査課","検査課"],"M14":["取付ない","取り付けない","取付けない","6点"],"M15":["±2%","±2％","2%以内","2％以内","C側"],"M16":["T側"]}
out=subprocess.run(["docker","exec","neo4j-kgb","cypher-shell","-u","neo4j","-p","kgb-please-change","--format","plain",
  "MATCH (c:Chunk)-[:PART_OF]->(d:Document) RETURN d.fileName + '§' + apoc.text.regreplace(c.text,'[\\\\s\\\\u3000]+',' ') AS x"],capture_output=True,text=True).stdout
norm=lambda s:" ".join(s.split())
CH=[]
for line in out.splitlines()[1:]:
    line=line.strip().strip('"')
    if '§' in line:
        f,t=line.split('§',1); CH.append((f,norm(t)))
print("chunks loaded:",len(CH))
def full(doc,prefix):
    p=norm(prefix.strip("� "))[:40]
    for f,t in CH:
        if f==doc and p in t: return t
    for f,t in CH:
        if p[:20] in t: return t
    return ""
for tag in sys.argv[1:]:
    T=json.load(open(f"{S}/trace_{tag}.json",encoding="utf-8"))
    for r in T:
        for mode,run in r["runs"].items():
            hits=[]
            for c in run["chunks"]:
                t=full(c["doc"],c["text"]); c["full_len"]=len(t)
                c["has_answer"]=[k for k in KEYS[r["id"]] if k in t]
                if c["has_answer"]: hits.append(c["doc"])
            run["chunks_with_answer"]=hits
            print(f"{tag} {r['id']} {mode[:12]:12s} 全文取得 {sum(1 for c in run['chunks'] if c['full_len'])}/{len(run['chunks'])}  答えの語を含むチャンク {len(hits)} {sorted(set(hits))}")
    json.dump(T,open(f"{S}/trace_{tag}.json","w",encoding="utf-8"),ensure_ascii=False,indent=1)
