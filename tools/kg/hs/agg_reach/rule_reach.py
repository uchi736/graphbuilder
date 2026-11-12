# 規則だけの名寄せ（飾りを外して一致・符号一致・括弧の別名・包含）を全体に当て、質問に出る語だけで入って 9 問の到達を数える
import json,base64,re,unicodedata,collections
rows=[json.loads(base64.b64decode(l.strip().strip('"'))) for l in open('chunks_kgb6_20261112.b64').read().split('\n')[1:] if l.strip()]
def nk(s): return re.sub(r'[\s_\-‐‑‒–—―−]+','',unicodedata.normalize('NFKC',s or '')).casefold()
def dk(s): return nk(s).replace('本文','').replace('生','')
TYPES={'Material','Specimen','TestItem','Process','Standard'}
PRE=re.compile(r'^(jis[a-z]\d{4}(\(\d{4}\))?|din\d+の?|特殊耐熱合金|ニレジスト|nire|\d項[\d.]*\d?\)?)')
SUF=re.compile(r'(又は相当材|または相当材|相当材|材|検査|試験|観察|測定|計測|試験片|号試験片|号)$')
KANA=[('及び','および'),('又は','または'),('焼き','焼'),('テイ','ティ'),('グロ','クロ')]
def strip(k):
    k=re.sub(r'[、,・/]','',k)
    for a,b in KANA: k=k.replace(a,b)
    for _ in range(3):
        k2=SUF.sub('',PRE.sub('',k))
        if k2==k or not k2: break
        k=k2
    return k
def core(s): return strip(re.sub(r'\(.*?\)','',nk(s)))
CODE=re.compile(r'[a-z]*\d[a-z0-9.]*')
def codes(s): return frozenset(c for c in CODE.findall(re.sub(r'\(.*?\)','',nk(s))) if len(c)>=3)
ent={}; docs=collections.defaultdict(set); chunks=collections.defaultdict(set)
for r in rows:
    for e in r['ents']:
        if e.get('id') and set(e['labels'] or [])&TYPES:
            ent[e['id']]=frozenset(set(e['labels'])&TYPES); docs[e['id']].add(dk(r['doc'])); chunks[e['id']].add(r['chunk'])
names=list(ent); C={n:core(n) for n in names}; K={n:codes(n) for n in names}
# 同じ物: 核が一致、または符号の集合が一致（符号が両方にあって違えば結ばない）。括弧の中が 1 語なら外側の核と別名
par=collections.defaultdict(set)
for n in names:
    m=re.fullmatch(r'(.{2,}?)\((.{2,}?)\)(.*)',nk(n))
    if m and not re.search(r'[、,]',m.group(2)) and not re.fullmatch(r'[\d.年版]+',m.group(2)):
        a,b=strip(m.group(1)+m.group(3)),strip(m.group(2))
        if a and b and a!=b: par[a].add(b); par[b].add(a)
uf={n:n for n in names}
def find(x):
    while uf[x]!=x: uf[x]=uf[uf[x]]; x=uf[x]
    return x
bycore=collections.defaultdict(list)
for n in names: bycore[C[n]].append(n)
links=collections.Counter()
def ok(a,b): return bool(ent[a]&ent[b]) and not (K[a] and K[b] and K[a]!=K[b])
for c,ns in bycore.items():
    for n in ns[1:]:
        if ok(ns[0],n) and find(ns[0])!=find(n): uf[find(n)]=find(ns[0]); links['核が一致']+=1
for a,bs in par.items():
    for b in bs:
        for x in bycore.get(a,[]):
            for y in bycore.get(b,[]):
                if ent[x]&ent[y] and find(x)!=find(y): uf[find(y)]=find(x); links['括弧の別名']+=1
grp=collections.defaultdict(set)
for n in names: grp[find(n)].add(n)
sizes=collections.Counter(len(v) for v in grp.values())
print('対象',len(names),'| 同じ物の線',dict(links),'| 2 個以上の組',sum(1 for v in grp.values() if len(v)>1),'最大',max(sizes),'| 大きい組の例',sorted([sorted(v) for v in grp.values() if len(v)>=5],key=len,reverse=True)[:3])
qs={q['qid']:q for q in json.load(open('../qa/questions_hs.json',encoding='utf-8'))['questions'] if q['type']=='agg'}
TERMS={'HS-10':r'sacm645','HS-11':r'scm440h','HS-15':r'クロムカーバイド|cr3c2','HS-16':r'y型b号','HS-17':r'jisz2201','HS-18':r'fcd400','HS-19':r'窒化','HS-20':r'qckai027|初品管理規定','HS-23':r'蛍光浸透探傷'}
tot=collections.Counter()
for qid,pat in TERMS.items():
    gold={dk(e['doc']) for e in qs[qid]['evidence']}
    e0={n for n in names if re.search(pat,nk(n))}
    e1=set().union(*[grp[find(n)] for n in e0]) if e0 else set()
    cores={C[n] for n in e1 if len(C[n])>=3}
    e2=e1|{n for n in names if any(c in C[n] or c in nk(n) for c in cores) and any(ent[n]&ent[m] for m in e1)}
    def f(es):
        d=set().union(*[docs[n] for n in es]) if es else set(); return len(gold&d),len(d-gold),len(set().union(*[chunks[n] for n in es]) if es else set())
    a,b,c=f(e0),f(e1),f(e2); tot['gold']+=len(gold); tot['e0']+=a[0]; tot['e1']+=b[0]; tot['e2']+=c[0]; tot['x0']+=a[1]; tot['x1']+=b[1]; tot['x2']+=c[1]
    tot['f0']+=a[0]==len(gold); tot['f1']+=b[0]==len(gold); tot['f2']+=c[0]==len(gold)
    print(f"{qid} 正解 {len(gold)} | 質問の語を含む名前 届く/余分/チャンク {a} | ＋同じ物 {b} | ＋狭い側 {c} | 足された名前 {sorted(e1-e0)[:6]} … {sorted(e2-e1)[:5]}")
print(dict(tot))
