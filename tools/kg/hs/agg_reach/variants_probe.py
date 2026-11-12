# 名寄せの方法の当たりを付ける（読み取りだけ）。段2=同じ種類で名前が別の名前を含む、段3=「A(B)」の括弧の別名
import json,base64,re,unicodedata,collections,itertools
rows=[json.loads(base64.b64decode(l.strip().strip('"'))) for l in open('chunks_kgb6_20261112.b64').read().split('\n')[1:] if l.strip()]
def nk(s): return re.sub(r'[\s_\-‐‑‒–—―−]+','',unicodedata.normalize('NFKC',s or '')).casefold()
ents={}; docs=collections.defaultdict(set)
for r in rows:
    for e in r['ents']:
        if e.get('id'): ents[e['id']]=tuple(sorted(e['labels'] or [])); docs[e['id']].add(r['doc'])
print('エンティティ',len(ents)); c=collections.Counter(l for ls in ents.values() for l in ls); print(c.most_common(14))
TYPES=['Material','Standard','Specimen','TestItem','Process']
by=collections.defaultdict(list)
for n,ls in ents.items():
    for l in ls:
        if l in TYPES: by[l].append(n)
# 段1 正規形が同じ
for t in TYPES:
    g=collections.defaultdict(list)
    for n in by[t]: g[nk(n)].append(n)
    same=sum(len(v)-1 for v in g.values() if len(v)>1)
    keys=sorted(g, key=len); pairs=[]
    for a in keys:
        if len(a)<4: continue
        for b in keys:
            if len(b)>len(a) and a in b: pairs.append((g[a][0],g[b][0]))
    cores={a for a,_ in pairs}; covered={b for _,b in pairs}
    print(f'\n[{t}] {len(by[t])} 個 | 段1 正規形で重なる {same} | 段2 含む関係 {len(pairs)} 本・核になる名前 {len(cores)}・ぶら下がる名前 {len(covered)}')
    for p in pairs[::max(1,len(pairs)//8)][:8]: print('    ',p[0],' ⊂ ',p[1])
par=[n for n in ents if re.search(r'.{2,}[(（][^()（）]{2,}[)）]',n) and set(ents[n])&set(TYPES)]
print('\n段3 括弧つきの名前',len(par)); 
for n in par[::max(1,len(par)//12)][:12]: print('    ',n,ents[n])
